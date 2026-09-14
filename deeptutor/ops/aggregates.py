"""Read-only aggregations over existing DeepTutor data (RIC-720, D3).

The ops dashboard is a *report over data the engine already writes* — no new
event engine. Five views:

* ``sales``        course / plan units sold and revenue, from the order ledger.
* ``coaching``     1v1 coaching revenue, from plans tagged for 1v1 delivery.
* ``renewal``      renewal rate — repeat purchases per learner.
* ``funnel``       learner progression across acquisition → activation →
                   engagement → monetization.
* ``mastery``      knowledge-point mastery across all mastery paths.

Every metric is computed from a real field (documented per metric); the API
never fabricates a number. Empty stores aggregate to zeroes, not errors.
"""

from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from deeptutor.ops.ranges import TimeRange

#: Plan tag that marks a plan as delivered via 1v1 coaching sessions.
COACHING_TAG = "coaching"

#: Plan ids that are the quintessential "1v1 coaching" upsell in the seeded
#: catalogue. Kept here so the renewal/sales views can attribute revenue even
#: before the operator tags plans explicitly.
COACHING_PLAN_IDS = frozenset({"plan_coach_basic", "plan_coach_pro"})

#: Order statuses counted as *paid* for revenue / sales.
PAID_STATUSES = frozenset({"paid", "granted", "completed"})

#: Mastery value that counts a knowledge point as mastered (the quantitative
#: gate used by the engine itself).
MASTERY_THRESHOLD = 0.9


def _tracked_value(token: Any, default: float = 0.0) -> float:
    """Best-effort numeric coercion of an order's tracked amount."""
    if token is None:
        return default
    try:
        return float(token)
    except (TypeError, ValueError):
        return default


def _round2(value: float) -> float:
    return round(float(value), 2)


def _pct(part: float, total: float) -> float:
    """Percent of ``part`` over ``total``, bounded at 0 when total is 0."""
    if total <= 0:
        return 0.0
    return _round2(float(part) / float(total) * 100.0)


def _dt_from_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp or Unix seconds into tz-aware UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def _coin_status(order: dict[str, Any]) -> str:
    return str(order.get("status") or "pending")


def _coin_amount(order: dict[str, Any]) -> float:
    return _tracked_value(order.get("amount_fen"))


def _coin_plan(order: dict[str, Any]) -> dict[str, Any]:
    plan = order.get("plan")
    return plan if isinstance(plan, dict) else {}


def _is_coaching_plan(plan: dict[str, Any]) -> bool:
    plan_id = str(plan.get("id") or "")
    tag = str(plan.get("tag") or "")
    if plan_id in COACHING_PLAN_IDS:
        return True
    return tag.lower() == COACHING_TAG


def _is_mastered(kp_value: Any) -> bool:
    try:
        return float(kp_value) >= MASTERY_THRESHOLD
    except (TypeError, ValueError):
        return False


def _order_created(order: dict[str, Any]) -> datetime | None:
    """Order creation as a tz-aware datetime (falls back to paid/granted)."""
    return (
        _dt_from_iso(order.get("created_at"))
        or _dt_from_iso(order.get("paid_at"))
        or _dt_from_iso(order.get("granted_at"))
    )


# ---------------------------------------------------------------------------
# Daily series helpers
# ---------------------------------------------------------------------------


def _bucket_series_by_day(start: datetime, end: datetime) -> OrderedDict[str, int]:
    """Ordered day → count map covering [start, end)."""
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    def _day_floor(dt: datetime) -> datetime:
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)

    days_bucket: OrderedDict[str, int] = OrderedDict()
    cursor = _day_floor(start)
    while cursor < end:
        days_bucket[cursor.date().isoformat()] = 0
        cursor = cursor + timedelta(days=1)
    return days_bucket


def _daily_series(
    items: list[tuple[datetime, float]],
    start: datetime,
    end: datetime,
) -> list[dict[str, float]]:
    """Bucket ``(timestamp, amount)`` pairs into an ordered daily series."""
    buckets = _bucket_series_by_day(start, end)
    for when, amount in items:
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        key = when.date().isoformat()
        if key in buckets:
            buckets[key] += amount
    return [{"date": key, "value": float(buckets[key])} for key in buckets]


# ---------------------------------------------------------------------------
# View builders (each consumes domain records, never raw files)
# ---------------------------------------------------------------------------


def build_sales_view(orders: list[dict[str, Any]], trange: TimeRange) -> dict[str, Any]:
    """Course / plan units sold and revenue in the window.

    Real fields: a paid order whose ``created_at`` (falling back to ``paid_at``/``granted_at``)
    lands in the window; revenue = ``amount_fen`` across those orders.
    """
    paid = [
        order
        for order in orders
        if _coin_status(order) in PAID_STATUSES
        and (created := _order_created(order)) is not None
        and trange.start <= created < trange.end
    ]
    unit_counts: Counter[str] = Counter()
    revenue = 0.0
    per_day: list[tuple[datetime, float]] = []
    for order in paid:
        plan = _coin_plan(order)
        plan_id = str(plan.get("id") or "unknown")
        unit_counts[plan_id] += 1
        amount = _coin_amount(order)
        revenue += amount
        created = _order_created(order)
        if created is not None:
            per_day.append((created, amount))

    plan_breakdown = [
        {
            "plan_id": plan_id,
            "units": count,
            "revenue_fen": round(
                sum(_coin_amount(o) for o in paid if _coin_plan(o).get("id") == plan_id)
            ),
        }
        for plan_id, count in unit_counts.most_common()
    ]
    return {
        "units_sold": len(paid),
        "revenue_fen": round(revenue),
        "plan_breakdown": plan_breakdown,
        "daily_revenue_fen": _daily_series(per_day, trange.start, trange.end),
    }


def build_coaching_view(orders: list[dict[str, Any]], trange: TimeRange) -> dict[str, Any]:
    """1v1 coaching revenue in the window.

    Attributes an order to coaching when its plan snapshot carries the
    ``coaching`` tag or a known coaching plan id.
    """
    coaching = [
        order
        for order in orders
        if _coin_status(order) in PAID_STATUSES
        and _is_coaching_plan(_coin_plan(order))
        and (created := _order_created(order)) is not None
        and trange.start <= created < trange.end
    ]
    revenue = round(sum(_coin_amount(order) for order in coaching))
    per_day = [
        (created, _coin_amount(order))
        for order in coaching
        if (created := _order_created(order)) is not None
    ]
    return {
        "sessions": len(coaching),
        "revenue_fen": revenue,
        "daily_revenue_fen": _daily_series(per_day, trange.start, trange.end),
    }


def build_renewal_view(orders: list[dict[str, Any]], trange: TimeRange) -> dict[str, Any]:
    """Renewal rate — repeat buyers in a cohort returning to buy again.

    A buyer counts as *renewal* when the window holds a paid order for a
    ``user_id`` that either already had a paid order before the window
    (a returning subscriber) or has more than one paid order inside the
    window (a same-window renewal). Rate = renewals / total window buyers.
    """
    paid_before_scope = {
        str(order.get("user_id") or "")
        for order in orders
        if _coin_status(order) in PAID_STATUSES
        and (created := _order_created(order)) is not None
        and created < trange.start
    }
    window_buyers: dict[str, list[dict[str, Any]]] = {}
    for order in orders:
        if _coin_status(order) not in PAID_STATUSES:
            continue
        created = _order_created(order)
        if created is None or not (trange.start <= created < trange.end):
            continue
        user_id = str(order.get("user_id") or "")
        window_buyers.setdefault(user_id, []).append(order)

    repeat = [
        user_id
        for user_id, buyer_orders in window_buyers.items()
        if user_id in paid_before_scope or len(buyer_orders) > 1
    ]
    total_buyers = len(window_buyers)
    return {
        "total_buyers": total_buyers,
        "renewal_buyers": len(repeat),
        "renewal_rate_pct": _pct(len(repeat), total_buyers),
    }


def build_funnel_view(
    *,
    visitors: int,
    active_learners: int,
    engaged_sessions: int,
    converted: int,
) -> dict[str, Any]:
    """Learner progression funnel over real counts (see aggregation assembler).

    * ``visitors``      registered accounts (``multi_user`` identity records).
    * ``active``        accounts with evidence of use (order or path activity).
    * ``engaged``       session count (proxy for sustained engagement).
    * ``converted``     accounts with at least one paid order.
    """
    visitors = max(0, int(visitors))

    def _rate(part: int) -> float:
        return _pct(part, visitors)

    return {
        "visitors": visitors,
        "active": max(0, int(active_learners)),
        "engaged_sessions": max(0, int(engaged_sessions)),
        "converted": max(0, int(converted)),
        "activation_rate_pct": _rate(active_learners),
        "engagement_rate_pct": _rate(engaged_sessions),
        "conversion_rate_pct": _rate(converted),
    }


def build_mastery_view(
    *,
    path_overviews: list[dict[str, Any]],
    kp_mastery: list[dict[str, Any]],
) -> dict[str, Any]:
    """Knowledge-point mastery across all paths (from ``mastery_path`` data).

    Each ``kp_mastery`` item is ``{"kp_id", "name", "module_name", "mastery", "mastered"}``.
    """
    total_kps = len(kp_mastery)
    mastered = sum(1 for kp in kp_mastery if kp["mastered"])
    avg = (
        _round2(sum(_tracked_value(kp.get("mastery")) for kp in kp_mastery) / total_kps)
        if total_kps
        else 0.0
    )

    by_module: dict[str, dict[str, Any]] = {}
    for kp in kp_mastery:
        module = str(kp.get("module_name") or "ungrouped")
        bucket = by_module.setdefault(
            module,
            {"module_name": module, "kp_count": 0, "mastered": 0, "avg_mastery": 0.0},
        )
        bucket["kp_count"] += 1
        if kp["mastered"]:
            bucket["mastered"] += 1
        bucket["avg_mastery"] += _tracked_value(kp.get("mastery"))
    for bucket in by_module.values():
        if bucket["kp_count"]:
            bucket["avg_mastery"] = _round2(bucket["avg_mastery"] / bucket["kp_count"])

    return {
        "paths": path_overviews,
        "modules": sorted(by_module.values(), key=lambda m: -m["mastered"]),
        "knowledge_points": kp_mastery,
        "totals": {
            "path_count": len(path_overviews),
            "kp_count": total_kps,
            "mastered_kp_count": mastered,
            "mastery_rate_pct": _pct(mastered, total_kps),
            "avg_mastery_pct": _round2(avg * 100.0),
        },
    }


# ---------------------------------------------------------------------------
# Assembled dashboard
# ---------------------------------------------------------------------------


@dataclass
class _Metric:
    key: str
    label: str
    value: str
    unit: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_dashboard_metrics(views: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _Metric("sales.units", "Course / plan units sold", str(views["sales"]["units_sold"])).to_dict(),
        _Metric(
            "sales.revenue", "Sales revenue", str(views["sales"]["revenue_fen"]), "fen"
        ).to_dict(),
        _Metric(
            "coaching.revenue", "1v1 coaching revenue", str(views["coaching"]["revenue_fen"]), "fen"
        ).to_dict(),
        _Metric(
            "renewal.rate", "Renewal rate", f'{views["renewal"]["renewal_rate_pct"]}%'
        ).to_dict(),
        _Metric(
            "funnel.conversion", "Conversion rate", f'{views["funnel"]["conversion_rate_pct"]}%'
        ).to_dict(),
        _Metric(
            "mastery.rate", "Mastery rate", f'{views["mastery"]["totals"]["mastery_rate_pct"]}%'
        ).to_dict(),
    ]


@dataclass
class OpsDashboard:
    """Wire shape of the full dashboard report."""

    range: str
    generated_at: str
    sales: dict[str, Any] = field(default_factory=dict)
    coaching: dict[str, Any] = field(default_factory=dict)
    renewal: dict[str, Any] = field(default_factory=dict)
    funnel: dict[str, Any] = field(default_factory=dict)
    mastery: dict[str, Any] = field(default_factory=dict)
    metrics: list[dict[str, Any]] = field(default_factory=list)
    learners: list[dict[str, Any]] = field(default_factory=list)
    sessions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


def convert_to_csv(rows: list[dict[str, Any]]) -> str:
    """Render a flat list of dicts as CSV (RFC-4180 safe).

    Columns follow first-encounter order across the rows.
    """
    if not rows:
        return ""
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)

    def _cell(value: Any) -> str:
        if value is None:
            return ""
        text = str(value)
        if any(ch in text for ch in (",", '"', "\n", "\r")):
            text = '"' + text.replace('"', '""') + '"'
        return text

    lines = [",".join(keys)] + [
        ",".join(_cell(row.get(key)) for key in keys) for row in rows
    ]
    return "\n".join(lines) + "\n"