"""API tests for the DeepTutor Enterprise license layer.

The router is mounted on a minimal FastAPI app (the existing API-test
pattern) so the heavy full-app lifespan — LLM client, coordination backend —
never runs.  The license-layer store roots are monkeypatched through
``deeptutor.multi_user.paths`` and the issuer key is pinned so no test can
touch the developer's real ``data/system`` tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI
from fastapi.testclient import TestClient

import deeptutor.api.routers.license as license_router_module
from deeptutor.license import service as license_service
from deeptutor.license import store as license_store
from deeptutor.multi_user import paths as mu_paths

TEST_ISSUER_KEY = "bb" * 32


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(license_router_module.router, prefix="/api/license")
    return app


@pytest.fixture()
def license_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setattr(mu_paths, "ADMIN_WORKSPACE_ROOT", data_root.resolve())
    monkeypatch.setattr(mu_paths, "USERS_ROOT", data_root / "users")
    monkeypatch.setattr(mu_paths, "SYSTEM_ROOT", data_root / "system")
    monkeypatch.setattr(mu_paths, "_path_services", {})
    license_service.mu_paths = mu_paths
    license_store.mu_paths = mu_paths
    monkeypatch.setenv(license_service.ENV_LICENSE_PRIVATE_KEY, TEST_ISSUER_KEY)
    return None


def _client():
    return TestClient(_build_app())


def test_customer_lifecycle(license_env: None) -> None:
    with _client() as client:
        resp = client.post(
            "/api/license/customers",
            json={"id": "acme", "name": "Acme Inc", "contact": "ops@acme"},
        )
        assert resp.status_code == 201
        customers = client.get("/api/license/customers").json()["customers"]
        assert any(c["id"] == "acme" for c in customers)


def test_issue_and_verify_via_api(license_env: None) -> None:
    with _client() as client:
        client.post("/api/license/customers", json={"id": "acme"})
        issued = client.post(
            "/api/license/licenses/issue",
            json={
                "customer_id": "acme",
                "granularity": "seats",
                "seats": 3,
                "features": ["sso"],
            },
        )
        assert issued.status_code == 200
        token = issued.json()["token"]
        verify = client.post("/api/license/verify", json={"token": token})
        assert verify.status_code == 200
        assert verify.json()["tenant_id"] == "acme"
        assert verify.json()["granularity"] == "seats"


def test_revoke_via_api(license_env: None) -> None:
    with _client() as client:
        client.post("/api/license/customers", json={"id": "acme"})
        issued = client.post(
            "/api/license/licenses/issue",
            json={"customer_id": "acme", "granularity": "org"},
        ).json()
        license_id = issued["license_id"]
        token = issued["token"]
        assert client.post(f"/api/license/licenses/{license_id}/revoke").status_code == 200
        assert client.post("/api/license/verify", json={"token": token}).status_code == 403
        assert (
            client.post(f"/api/license/licenses/{license_id}/unrevoke").status_code == 200
        )
        assert client.post("/api/license/verify", json={"token": token}).status_code == 200


def test_acquire_release_seat_via_api(license_env: None) -> None:
    with _client() as client:
        client.post("/api/license/customers", json={"id": "acme"})
        token = client.post(
            "/api/license/licenses/issue",
            json={"customer_id": "acme", "granularity": "seats", "seats": 1},
        ).json()["token"]
        seat = client.post(
            "/api/license/acquire-seat", json={"token": token, "learner_id": "l1"}
        )
        assert seat.status_code == 200
        assert seat.json()["seat_id"]
        blocked = client.post(
            "/api/license/acquire-seat", json={"token": token, "learner_id": "l2"}
        )
        assert blocked.status_code == 403
        client.post(
            "/api/license/release-seat", json={"token": token, "learner_id": "l1"}
        )
        freed = client.post(
            "/api/license/acquire-seat", json={"token": token, "learner_id": "l2"}
        )
        assert freed.status_code == 200


def test_tenant_mismatch_rejected(license_env: None) -> None:
    with _client() as client:
        client.post("/api/license/customers", json={"id": "acme"})
        token = client.post(
            "/api/license/licenses/issue",
            json={"customer_id": "acme", "tenant_id": "acme-ops", "granularity": "org"},
        ).json()["token"]
        bad = client.post(
            "/api/license/verify", json={"token": token, "tenant_id": "other"}
        )
        assert bad.status_code == 403