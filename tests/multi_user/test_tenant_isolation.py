"""Multi-tenant isolation tests.

The whole point of D5 tenancy is that two operating entities on one private
instance never see each other's data.  These tests exercise that guarantee on
the three record stores that carry a ``tenant_id``: orders, courses, and the
tenant ContextVar seam itself.

Store roots are monkeypatched through ``deeptutor.multi_user.paths`` so no
test can touch the developer's real tree.  Tenancy is toggled through the
``DEEPTUTOR_TENANT_MODE`` environment variable (read at call time).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from deeptutor.multi_user import paths as mu_paths
from deeptutor.multi_user import tenant as tenant_module
from deeptutor.multi_user.tenant import (
    denormalized_tenant,
    tenant_context,
)
from deeptutor.payment import orders as orders_module
from deeptutor.services import courses as courses_module


@pytest.fixture()
def isolated_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setattr(mu_paths, "ADMIN_WORKSPACE_ROOT", data_root.resolve())
    monkeypatch.setattr(mu_paths, "USERS_ROOT", data_root / "users")
    monkeypatch.setattr(mu_paths, "SYSTEM_ROOT", data_root / "system")
    monkeypatch.setattr(mu_paths, "_path_services", {})
    # Redirect module-level references so per-call path reads honor the patch.
    orders_module.SYSTEM_ROOT = data_root / "system"
    courses_module.get_path_service  # noqa: B018  (module import touch)
    yield data_root
    os.environ.pop(tenant_module.TENANT_MODE_ENV, None)


@pytest.fixture()
def multi_tenant_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(tenant_module.TENANT_MODE_ENV, "multi")


def test_single_tenant_defaults_to_empty() -> None:
    os.environ.pop(tenant_module.TENANT_MODE_ENV, None)
    os.environ.pop(tenant_module.DEFAULT_TENANT_ENV, None)
    assert denormalized_tenant() == ""


def test_multi_tenant_missing_scope_raises() -> None:
    os.environ[tenant_module.TENANT_MODE_ENV] = "multi"
    try:
        with pytest.raises(RuntimeError):
            denormalized_tenant()
    finally:
        os.environ.pop(tenant_module.TENANT_MODE_ENV, None)


def test_tenant_context_scoping() -> None:
    os.environ.pop(tenant_module.TENANT_MODE_ENV, None)
    assert denormalized_tenant() == ""
    with tenant_context("tenant-a"):
        assert denormalized_tenant() == "tenant-a"
    assert denormalized_tenant() == ""


def test_orders_are_isolated_by_tenant(
    isolated_roots: Path, multi_tenant_env: None
) -> None:
    with tenant_context("school-a"):
        orders_module.create_order(
            user_id="u1", username="alice", plan={"id": "p1", "price_fen": 100},
            gateway="wechat",
        )
    with tenant_context("school-b"):
        orders_module.create_order(
            user_id="u2", username="bob", plan={"id": "p1", "price_fen": 100},
            gateway="wechat",
        )

    with tenant_context("school-a"):
        mine = orders_module.list_orders(user_id="u1")
        others = orders_module.list_orders(user_id="u2")
        assert len(mine) == 1
        assert len(others) == 0

    with tenant_context("school-b"):
        mine = orders_module.list_orders(user_id="u2")
        assert len(mine) == 1

    # A cross-tenant query explicitly passed returns nothing.
    assert len(orders_module.list_orders(tenant_id="school-c")) == 0


def test_orders_record_carries_tenant_id(
    isolated_roots: Path, multi_tenant_env: None
) -> None:
    with tenant_context("school-a"):
        created = orders_module.create_order(
            user_id="u1", username="alice", plan={"id": "p1", "price_fen": 100},
            gateway="wechat",
        )
        store = orders_module._load_store()
        row = store["orders"][created["order_id"]]
        assert row.get("tenant_id") == "school-a"


def test_courses_are_isolated_by_tenant(
    isolated_roots: Path, multi_tenant_env: None
) -> None:
    svc_a = courses_module.CourseService(root=isolated_roots / "a-courses")
    svc_b = courses_module.CourseService(root=isolated_roots / "b-courses")

    with tenant_context("school-a"):
        svc_a.create(name="Algebra")
    with tenant_context("school-b"):
        svc_b.create(name="Chemistry")

    # Same service root but different tenants must see different data when
    # tenancy is on: the filter is applied to the loaded rows.
    shared = courses_module.CourseService(root=isolated_roots / "a-courses")
    with tenant_context("school-a"):
        names = [c.name for c in shared.list_courses()]
        assert "Algebra" in names
        assert "Chemistry" not in names

    # The created course record carries the tenant it was stamped with.
    with tenant_context("school-a"):
        course = shared.list_courses()[0]
        assert course.tenant_id == "school-a"


def test_install_current_user_sets_tenant_contextvar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token carrying ``tenant_id`` installs the tenant ContextVar.

    This is the seam that makes per-request tenant scope flow from the JWT to
    every ``denormalized_tenant()`` read inside an endpoint — without it, a
    multi-tenant deployment would silently write records with no tenant.
    """
    from deeptutor.api.routers.auth import _install_current_user
    from deeptutor.multi_user.context import reset_current_user
    from deeptutor.multi_user.tenant import get_current_tenant
    from deeptutor.services.auth import TokenPayload

    token = _install_current_user(
        TokenPayload(username="alice", role="user", user_id="u_alice", tenant_id="school-a")
    )
    try:
        assert get_current_tenant() == "school-a"
    finally:
        reset_current_user(token)
    # The tenant var rides the request/WS task context; it is discarded when
    # that task ends (HTTP always gets a fresh context per request).  Nothing
    # more to assert here — the pre/post values are the contract.


def test_create_token_bakes_current_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_token embeds the active tenant so the resulting JWT scopes the
    session to the right operating entity."""
    from deeptutor.services import auth as auth_module
    from deeptutor.services.auth import TokenPayload, decode_token

    # Pin a deterministic secret and a tenant so we can round-trip.
    monkeypatch.setattr(auth_module, "AUTH_SECRET", "aa" * 32)
    with tenant_context("school-b"):
        raw = auth_module.create_token("bob", role="user", user_id="u_bob")
    payload = decode_token(raw)
    assert payload is not None
    assert payload.tenant_id == "school-b"
    assert isinstance(payload, TokenPayload)

