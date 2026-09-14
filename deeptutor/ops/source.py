"""Assemble raw domain records into an :class:`OpsDashboard`.

This module is the only place that touches persistence. It reads through the
genuine domain stores (never raw files) so the dashboard reflects exactly
what the engine has written:

* order ledger    -> ``deeptutor.payment.orders.list_order_records``
* accounts        -> ``deeptutor.multi_user.identity.list_user_info``
* mastery paths   -> ``deeptutor.learning.service.LearningService.list_progress``
* session/activity-> the unified session store (``sessions``/``turns``)

Reading through services keeps the aggregations robust to multi-user scoping
and store location; the dashboard never writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any

from deeptutor.ops.aggregates import (
    OpsDashboard,
    _is_mastered,
    _order_created,
    _tracked_value,
    build_coaching_view,
    build_dashboard_metrics,
    build_funnel_view,
    build_mastery_view,
    build_renewal_view,
    build_sales_view,
)
from deeptutor.ops.ranges import TimeRange

logger = logging.getLogger(__name__)

MAX_PATH_OVERVIEWS = 200


@dataclass
class DashboardSnapshot:
    """Everything the aggregation layer reads from persistence, pre-computed."""

    trange: TimeRange
    orders: list[dict[str, Any]] = None  # type: ignore[assignment]
    users: list[dict[str, Any]] = None  # type: ignore[assignment]
    path_overviews: list[dict[str, Any]] = None  # type: ignore[assignment]
    kp_mastery: list[dict[str, Any]] = None  # type: ignore[assignment]
    session_count: int = 0
    paid_user_ids: set[str] = None  # type: ignore[assignment]


def _load_orders() -> list[dict[str, Any]]:
    from deeptutor.payment.orders import list_order_records

    try:
        return list_order_records(limit=10_000)
    except Exception:
        logger.warning("ops: failed to read order ledger", exc_info=True)
        return []


def _load_users() -> list[dict[str, Any]]:
    from deeptutor.multi_user.identity import list_user_info

    try:
        return list_user_info()
    except Exception:
        logger.warning("ops: failed to read user registry", exc_info=True)
        return []


def _load_mastery() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(path_overviews, kp_mastery)`` from the mastery store."""
    from deeptutor.learning.service import LearningService

    try:
        service = LearningService()
        result = service.list_progress()
    except Exception:
        logger.warning("ops: failed to read mastery progress", exc_info=True)
        return [], []

    summaries = result.get("summaries") if isinstance(result, dict) else []
    if not isinstance(summaries, list):
        summaries = []

    kp_mastery: list[dict[str, Any]] = []
    for summary in summaries[:MAX_PATH_OVERVIEWS]:
        try:
            progress = service.store.load(str(summary.get("book_id") or ""))
        except Exception:
            continue
        if progress is None:
            continue
        module_by_kp: dict[str, str] = {}
        for module in progress.modules:
            for kp in module.knowledge_points:
                module_by_kp[kp.id] = module.name
        for kp_id, level in (progress.mastery_levels or {}).items():
            mastered = _is_mastered(level)
            kp_mastery.append(
                {
                    "kp_id": kp_id,
                    "name": kp_id,
                    "module_name": module_by_kp.get(kp_id, ""),
                    "mastery": _tracked_value(level),
                    "mastered": mastered,
                }
            )
        # Qualitative (CONCEPT/DESIGN) mastery is a boolean pass; expose each
        # as a mastered knowledge point with a nominal 1.0 (matching policy).
        for kp_id, passed in (progress.qualitative_mastery or {}).items():
            module_name = module_by_kp.get(kp_id, "")
            kp_mastery.append(
                {
                    "kp_id": kp_id,
                    "name": kp_id,
                    "module_name": module_name,
                    "mastery": 1.0 if passed else 0.0,
                    "mastered": bool(passed),
                }
            )

    return list(summaries), kp_mastery


async def _load_session_count() -> int:
    """Total sessions across the unified session store (best effort)."""
    from deeptutor.services.session import get_session_store

    try:
        store = get_session_store()
        sessions = await store.list_sessions(limit=10_000)
        return len(sessions) if sessions else 0
    except Exception:
        logger.warning("ops: failed to count sessions", exc_info=True)
        return 0


def _paid_user_ids(orders: list[dict[str, Any]], trange: TimeRange) -> set[str]:
    """Unique user ids with a paid order in the window (or any, pre-window)."""
    out: set[str] = set()
    for order in orders:
        created = _order_created(order)
        if created is None or not (trange.start <= created < trange.end):
            continue
        if str(order.get("status") or "") in {"paid", "granted", "completed"}:
            out.add(str(order.get("user_id") or ""))
    return out


async def collect_snapshot(trange: TimeRange, *, now: datetime | None = None) -> DashboardSnapshot:
    """Load every domain record the dashboard needs, once per request."""
    now = now or datetime.now(timezone.utc)
    orders = _load_orders()
    users = _load_users()
    path_overviews, kp_mastery = _load_mastery()
    session_count = await _load_session_count()
    paid_user_ids = _paid_user_ids(orders, trange)
    return DashboardSnapshot(
        trange=trange,
        orders=orders,
        users=users,
        path_overviews=path_overviews,
        kp_mastery=kp_mastery,
        session_count=session_count,
        paid_user_ids=paid_user_ids,
    )


async def build_dashboard(trange: TimeRange, *, now: datetime | None = None) -> OpsDashboard:
    """Assemble the full dashboard report for the given time range."""
    now = now or datetime.now(timezone.utc)
    snap = await collect_snapshot(trange, now=now)

    sales = build_sales_view(snap.orders, trange)
    coaching = build_coaching_view(snap.orders, trange)
    renewal = build_renewal_view(snap.orders, trange)

    converted = len(snap.paid_user_ids)
    active_learners = converted
    path_overviews = snap.path_overviews
    if path_overviews:
        active_learners += sum(
            1 for overview in path_overviews if str(overview.get("book_id") or "")
        )
    active_learners = min(active_learners, max(len(snap.users), 1))

    funnel = build_funnel_view(
        visitors=len(snap.users),
        active_learners=active_learners,
        engaged_sessions=snap.session_count,
        converted=converted,
    )
    mastery = build_mastery_view(
        path_overviews=path_overviews,
        kp_mastery=snap.kp_mastery,
    )

    views = {
        "sales": sales,
        "coaching": coaching,
        "renewal": renewal,
        "funnel": funnel,
        "mastery": mastery,
    }

    learners = [
        {
            "user_id": str(user.get("id") or ""),
            "username": str(user.get("username") or ""),
            "role": str(user.get("role") or "user"),
            "created_at": str(user.get("created_at") or ""),
            "disabled": bool(user.get("disabled", False)),
        }
        for user in snap.users
    ]

    return OpsDashboard(
        range=trange.key,
        generated_at=now.isoformat(),
        sales=sales,
        coaching=coaching,
        renewal=renewal,
        funnel=funnel,
        mastery=mastery,
        metrics=build_dashboard_metrics(views),
        learners=learners,
        sessions=[],
    )