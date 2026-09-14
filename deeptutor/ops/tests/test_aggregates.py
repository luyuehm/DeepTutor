"""Tests for the ops-dashboard aggregation layer (RIC-720, D3).

The view builders are pure: they consume plain order/account/mastery records
so the assertions are deterministic without touching persistence. The API
router tests exercise the mounted endpoints through a FastAPI TestClient with
the source layer monkeypatched, mirroring the payment test convention.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from deeptutor.ops.aggregates import (
    build_coaching_view,
    build_funnel_view,
    build_mastery_view,
    build_renewal_view,
    build_sales_view,
    convert_to_csv,
)
from deeptutor.ops.ranges import TimeRange, resolve_range

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def _tr(key: str = "30d") -> TimeRange:
    return resolve_range(key, now=NOW)


def _paid_order(order_id: str, user_id: str, plan: dict, amount_fen: int, created: str) -> dict:
    return {
        "order_id": order_id,
        "user_id": user_id,
        "username": f"user_{user_id}",
        "status": "granted",
        "amount_fen": amount_fen,
        "created_at": created,
        "paid_at": created,
        "granted_at": created,
        "plan": plan,
    }


def _coaching_plan(plan_id: str = "plan_coach_basic") -> dict:
    return {"id": plan_id, "name": "1v1 陪练", "price_fen": 9900, "tag": "coaching"}


def _standard_plan(plan_id: str = "p1") -> dict:
    return {"id": plan_id, "name": "标准版", "price_fen": 2990, "tag": ""}


# ---------------------------------------------------------------------------
# Sales view
# ---------------------------------------------------------------------------


class TestSalesView:
    def test_counts_only_paid_orders_in_window(self):
        tr = _tr()
        orders = [
            _paid_order("1", "u1", _standard_plan(), 2990, "2026-09-01T00:00:00+00:00"),
            _paid_order("2", "u1", _coaching_plan(), 9900, "2026-09-05T00:00:00+00:00"),
            {**_paid_order("3", "u2", _standard_plan(), 2990, "2026-09-10T00:00:00+00:00"), "status": "pending"},
            {**_paid_order("4", "u3", _standard_plan(), 2990, "2026-09-12T00:00:00+00:00"), "status": "refunded"},
            _paid_order("5", "u4", _standard_plan(), 2990, "2026-07-01T00:00:00+00:00"),  # before window
        ]
        view = build_sales_view(orders, tr)
        assert view["units_sold"] == 2
        assert view["revenue_fen"] == 12890  # 2990 + 9900
        assert {b["plan_id"] for b in view["plan_breakdown"]} == {"p1", "plan_coach_basic"}

    def test_daily_series_covers_window_and_zero_fills(self):
        tr = _tr("7d")
        orders = [_paid_order("1", "u1", _standard_plan(), 2990, "2026-09-13T00:00:00+00:00")]
        view = build_sales_view(orders, tr)
        dates = [row["date"] for row in view["daily_revenue_fen"]]
        # 7d window from 09-07 12:00 -> 09-14 12:00 spans 8 distinct dates.
        assert len(dates) == 8
        assert dates[-1] == "2026-09-14"
        # The single order lands on 2026-09-13.
        assert view["daily_revenue_fen"][-2]["value"] == 2990


# ---------------------------------------------------------------------------
# Coaching view
# ---------------------------------------------------------------------------


class TestCoachingView:
    def test_attributes_coaching_plan(self):
        tr = _tr()
        orders = [
            _paid_order("1", "u1", _coaching_plan("plan_coach_basic"), 9900, "2026-09-01T00:00:00+00:00"),
            _paid_order("2", "u1", _coaching_plan("plan_coach_pro"), 19900, "2026-09-02T00:00:00+00:00"),
            _paid_order("3", "u2", _standard_plan(), 2990, "2026-09-03T00:00:00+00:00"),
        ]
        view = build_coaching_view(orders, tr)
        assert view["sessions"] == 2
        assert view["revenue_fen"] == 29800  # 9900 + 19900

    def test_plan_with_coaching_tag_counts(self):
        tr = _tr()
        plan = {"id": "custom", "name": "专属陪练", "price_fen": 5000, "tag": "Coaching"}
        orders = [_paid_order("1", "u1", plan, 5000, "2026-09-01T00:00:00+00:00")]
        view = build_coaching_view(orders, tr)
        assert view["sessions"] == 1
        assert view["revenue_fen"] == 5000


# ---------------------------------------------------------------------------
# Renewal view
# ---------------------------------------------------------------------------


class TestRenewalView:
    def test_renewal_from_pre_window_purchase(self):
        tr = _tr()
        orders = [
            _paid_order("1", "u1", _standard_plan(), 2990, "2026-08-01T00:00:00+00:00"),  # before window
            _paid_order("2", "u1", _standard_plan(), 2990, "2026-09-01T00:00:00+00:00"),  # renewal
            _paid_order("3", "u2", _standard_plan(), 2990, "2026-09-02T00:00:00+00:00"),  # new buyer
        ]
        view = build_renewal_view(orders, tr)
        assert view["total_buyers"] == 2
        assert view["renewal_buyers"] == 1
        assert view["renewal_rate_pct"] == 50.0

    def test_same_window_repeat_counts_as_renewal(self):
        tr = _tr()
        orders = [
            _paid_order("1", "u1", _standard_plan(), 2990, "2026-09-01T00:00:00+00:00"),
            _paid_order("2", "u1", _standard_plan(), 2990, "2026-09-05T00:00:00+00:00"),
            _paid_order("3", "u2", _standard_plan(), 2990, "2026-09-06T00:00:00+00:00"),
        ]
        view = build_renewal_view(orders, tr)
        assert view["total_buyers"] == 2
        assert view["renewal_buyers"] == 1

    def test_empty_window_has_zero_rate(self):
        tr = _tr()
        view = build_renewal_view([], tr)
        assert view["total_buyers"] == 0
        assert view["renewal_buyers"] == 0
        assert view["renewal_rate_pct"] == 0.0


# ---------------------------------------------------------------------------
# Funnel view
# ---------------------------------------------------------------------------


class TestFunnelView:
    def test_rates_computed_from_counts(self):
        view = build_funnel_view(visitors=100, active_learners=20, engaged_sessions=50, converted=3)
        assert view["visitors"] == 100
        assert view["active"] == 20
        assert view["engaged_sessions"] == 50
        assert view["converted"] == 3
        assert view["activation_rate_pct"] == 20.0
        assert view["conversion_rate_pct"] == 3.0

    def test_zero_visitors_guards_division(self):
        view = build_funnel_view(visitors=0, active_learners=0, engaged_sessions=0, converted=0)
        assert view["conversion_rate_pct"] == 0.0


# ---------------------------------------------------------------------------
# Mastery view
# ---------------------------------------------------------------------------


class TestMasteryView:
    def test_rolls_up_modules_and_totals(self):
        kps = [
            {"kp_id": "k1", "name": "k1", "module_name": "M1", "mastery": 0.95, "mastered": True},
            {"kp_id": "k2", "name": "k2", "module_name": "M1", "mastery": 0.5, "mastered": False},
            {"kp_id": "k3", "name": "k3", "module_name": "M2", "mastery": 0.7, "mastered": False},
        ]
        view = build_mastery_view(path_overviews=[{"book_id": "b1"}], kp_mastery=kps)
        totals = view["totals"]
        assert totals["path_count"] == 1
        assert totals["kp_count"] == 3
        assert totals["mastered_kp_count"] == 1
        assert totals["mastery_rate_pct"] == 33.33
        # M1 averages (0.95 + 0.5) / 2 = 0.725 (module rollups stay on the
        # 0..1 mastery scale; only the headline "avg_mastery_pct" is a percent).
        modules = {m["module_name"]: m for m in view["modules"]}
        assert modules["M1"]["avg_mastery"] == 0.72

    def test_empty_mastery_is_zero(self):
        view = build_mastery_view(path_overviews=[], kp_mastery=[])
        assert view["totals"]["kp_count"] == 0
        assert view["totals"]["mastery_rate_pct"] == 0.0
        assert view["totals"]["avg_mastery_pct"] == 0.0


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


class TestCsv:
    def test_escapes_quotes_and_commas(self):
        rows = [
            {"plan_id": "p1", "units": 1, "note": 'a,"quoted",note'},
            {"plan_id": "p2", "units": 2, "note": "line\nbreak"},
        ]
        csv_text = convert_to_csv(rows)
        assert csv_text.startswith("plan_id,units,note")
        assert '"a,""quoted"",note"' in csv_text
        assert '"line\nbreak"' in csv_text

    def test_empty_rows(self):
        assert convert_to_csv([]) == ""


# ---------------------------------------------------------------------------
# Time range resolution
# ---------------------------------------------------------------------------


class TestRangeResolution:
    def test_unknown_range_falls_back_to_default(self):
        tr = resolve_range("bogus", now=NOW)
        assert tr.key == "30d"

    def test_all_range_has_wide_window(self):
        tr = resolve_range("all", now=NOW)
        assert tr.key == "all"
        assert tr.start <= NOW


# ---------------------------------------------------------------------------
# API endpoints (TestClient with patched source)
# ---------------------------------------------------------------------------


@pytest.fixture
def ops_app(tmp_path, monkeypatch):
    """App with the ops router mounted and the source layer stubbed."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from deeptutor.api.routers.auth import require_admin
    from deeptutor.api.routers.ops_dashboard import router

    app = FastAPI()
    app.include_router(router, prefix="/api/ops")
    app.dependency_overrides[require_admin] = lambda: None

    from deeptutor.ops.ranges import TimeRange

    async def _fake_dashboard(trange: TimeRange):
        from deeptutor.ops.aggregates import OpsDashboard

        return OpsDashboard(
            range=trange.key,
            generated_at="2026-09-14T12:00:00+00:00",
            sales={
                "units_sold": 4,
                "revenue_fen": 12890,
                "plan_breakdown": [
                    {"plan_id": "p1", "units": 3, "revenue_fen": 8970},
                    {"plan_id": "plan_coach_basic", "units": 1, "revenue_fen": 3920},
                ],
                "daily_revenue_fen": [],
            },
            coaching={"sessions": 1, "revenue_fen": 3920, "daily_revenue_fen": []},
            renewal={"total_buyers": 3, "renewal_buyers": 1, "renewal_rate_pct": 33.33},
            funnel={
                "visitors": 10,
                "active": 4,
                "engaged_sessions": 60,
                "converted": 2,
                "activation_rate_pct": 40.0,
                "engagement_rate_pct": 60.0,
                "conversion_rate_pct": 20.0,
            },
            mastery={
                "paths": [{"book_id": "b1"}],
                "modules": [],
                "knowledge_points": [],
                "totals": {
                    "path_count": 1,
                    "kp_count": 3,
                    "mastered_kp_count": 1,
                    "mastery_rate_pct": 33.33,
                    "avg_mastery_pct": 72.0,
                },
            },
            metrics=[
                {"key": "sales.units", "label": "Units", "value": "4", "unit": ""},
            ],
            learners=[
                {"user_id": "u1", "username": "alice", "role": "user", "created_at": "", "disabled": False}
            ],
            sessions=[],
        )

    monkeypatch.setattr(
        "deeptutor.api.routers.ops_dashboard.build_dashboard",
        _fake_dashboard,
    )
    return TestClient(app)


class TestOpsApi:
    def test_dashboard_endpoint(self, ops_app):
        resp = ops_app.get("/api/ops?range=30d")
        assert resp.status_code == 200
        body = resp.json()
        assert body["range"] == "30d"
        assert body["sales"]["units_sold"] == 4
        assert body["renewal"]["renewal_rate_pct"] == 33.33

    def test_csv_export(self, ops_app):
        resp = ops_app.get("/api/ops/export?fmt=csv&range=30d")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert "view,key,label,value,unit" in resp.text
        assert "sales.units" in resp.text

    def test_json_export(self, ops_app):
        resp = ops_app.get("/api/ops/export?fmt=json&range=7d")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        rows = resp.json()
        assert isinstance(rows, list)
        assert any(row["key"] == "sales.units" for row in rows)

    def test_learners_endpoint(self, ops_app):
        resp = ops_app.get("/api/ops/learners")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["learners"][0]["username"] == "alice"

    def test_invalid_range_rejected(self, ops_app):
        resp = ops_app.get("/api/ops?range=forever")
        assert resp.status_code == 422

# ---------------------------------------------------------------------------
# Integration: aggregation reads real persisted order data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_dashboard_reads_seeded_orders(tmp_path, monkeypatch):
    """Seed the order ledger through the real domain store and confirm the
    dashboard aggregates real fields (no fabrication)."""

    # Isolate the payment/multi-user store under tmp_path (mirrors
    # tests/payment/test_orders.py's payment_env).
    admin_root = (tmp_path / "data").resolve()
    system_root = admin_root / "system"

    from deeptutor.multi_user import audit, grants, identity
    from deeptutor.multi_user import paths as mpaths
    from deeptutor.payment import orders as orders_mod

    for module in (mpaths, identity, grants, audit):
        monkeypatch.setattr(module, "SYSTEM_ROOT", system_root)
    monkeypatch.setattr(mpaths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(mpaths, "ADMIN_WORKSPACE_ROOT", admin_root)
    monkeypatch.setattr(mpaths, "USERS_ROOT", admin_root / "users")
    monkeypatch.setattr(mpaths, "LEGACY_MULTI_USER_ROOT", tmp_path / "multi-user")
    monkeypatch.setattr(mpaths, "_path_services", {})
    monkeypatch.setattr(identity, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(identity, "AUTH_DIR", system_root / "auth")
    monkeypatch.setattr(identity, "USERS_FILE", system_root / "auth" / "users.json")
    monkeypatch.setattr(identity, "SECRET_FILE", system_root / "auth" / "auth_secret")
    monkeypatch.setattr(orders_mod, "SYSTEM_ROOT", system_root)

    system_root.mkdir(parents=True, exist_ok=True)

    from deeptutor.payment.orders import create_order, transition_order

    # Learner u1 buys standard once, then renews; u2 buys coaching.
    o1 = create_order(
        user_id="u1",
        username="alice",
        plan={"id": "p1", "name": "标准版", "price_fen": 2990},
        gateway="epay",
    )
    transition_order(o1["order_id"], "paid")
    transition_order(o1["order_id"], "granted")
    o2 = create_order(
        user_id="u1",
        username="alice",
        plan={"id": "p1", "name": "标准版", "price_fen": 2990},
        gateway="epay",
    )
    transition_order(o2["order_id"], "paid")
    transition_order(o2["order_id"], "granted")
    o3 = create_order(
        user_id="u2",
        username="bob",
        plan={"id": "plan_coach_basic", "name": "1v1 陪练", "price_fen": 9900, "tag": "coaching"},
        gateway="wechat",
    )
    transition_order(o3["order_id"], "paid")
    transition_order(o3["order_id"], "granted")

    # Users registry drives the funnel visitors count.
    identity.save_user("root", "hash", role="admin")
    identity.save_user("alice", "hash", role="user")
    identity.save_user("bob", "hash", role="user")

    # Stub session count (the real session store is not touched in this test).
    from deeptutor.ops import source as ops_source

    async def _fake_session_count() -> int:
        return 0

    monkeypatch.setattr(ops_source, "_load_session_count", _fake_session_count)

    from datetime import datetime, timezone

    from deeptutor.ops.ranges import resolve_range

    # Orders are stamped with the real wall clock; resolve the range at the
    # same instant so the ``all`` window covers them.
    tr = resolve_range("all", now=datetime.now(timezone.utc))
    report = await ops_source.build_dashboard(tr)
    data = report.to_dict()

    assert data["sales"]["units_sold"] == 3
    assert data["sales"]["revenue_fen"] == 2990 * 2 + 9900
    assert data["coaching"]["sessions"] == 1
    assert data["coaching"]["revenue_fen"] == 9900
    # u1 bought twice (renewal), u2 once -> 1 renewal / 2 buyers.
    assert data["renewal"]["total_buyers"] == 2
    assert data["renewal"]["renewal_buyers"] == 1
    assert data["renewal"]["renewal_rate_pct"] == 50.0
    # 3 registered accounts.
    assert data["funnel"]["visitors"] == 3
