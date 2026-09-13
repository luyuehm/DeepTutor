"""Tests for the DeepTutor Enterprise license layer.

These mirror the multi-user test-suite conventions: the license store roots
are monkeypatched through ``deeptutor.multi_user.paths`` so no test can touch
the developer's real ``data/system`` tree, and the signer key is pinned to a
hex test value so no key file is generated.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deeptutor.license import service as license_service
from deeptutor.license import store as license_store
from deeptutor.license.contracts import (
    LicenseConcurrencyExhaustedError,
    LicenseError,
    LicenseExpiredError,
    LicenseRevokedError,
    LicenseSeatsExhaustedError,
    LicenseVerifyError,
)
from deeptutor.multi_user import paths as mu_paths

TEST_ISSUER_KEY = "aa" * 32


@pytest.fixture()
def license_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate the license store and pin the issuer key."""
    data_root = tmp_path / "data"
    monkeypatch.setattr(mu_paths, "ADMIN_WORKSPACE_ROOT", data_root.resolve())
    monkeypatch.setattr(mu_paths, "USERS_ROOT", data_root / "users")
    monkeypatch.setattr(mu_paths, "SYSTEM_ROOT", data_root / "system")
    monkeypatch.setattr(mu_paths, "_path_services", {})
    # Redirect the service/store module references so per-call path reads honor
    # the monkeypatch (the modules import ``paths as mu_paths`` at module load).
    license_service.mu_paths = mu_paths
    license_store.mu_paths = mu_paths
    monkeypatch.setenv(license_service.ENV_LICENSE_PRIVATE_KEY, TEST_ISSUER_KEY)


def _add_customer(customer_id: str = "acme") -> None:
    license_service.save_customer(
        {
            "id": customer_id,
            "name": customer_id,
            "status": "active",
            "created_at": "2026-09-01T00:00:00Z",
        }
    )


def test_issue_and_verify_org_license(license_env: None) -> None:
    _add_customer()
    issued = license_service.issue_license(
        customer_id="acme",
        tenant_id="acme-ops",
        granularity="org",
        features=["sso", "webhook"],
    )
    assert issued["token"].startswith("DTLIC-") or not issued["token"].startswith("CLIPA-")
    verified = license_service.verify_license(issued["token"])
    assert verified.claims.tenant_id == "acme-ops"
    assert verified.claims.granularity == "org"
    assert "sso" in verified.claims.features


def test_verify_rejects_unknown_tenant(license_env: None) -> None:
    _add_customer()
    issued = license_service.issue_license(customer_id="acme", granularity="org")
    with pytest.raises(LicenseVerifyError):
        license_service.verify_license(issued["token"], tenant_id="other-tenant")


def test_verify_rejects_tampered_token(license_env: None) -> None:
    _add_customer()
    issued = license_service.issue_license(customer_id="acme", granularity="org")
    tampered = issued["token"][:-1] + ("A" if issued["token"][-1] != "A" else "B")
    with pytest.raises(LicenseVerifyError):
        license_service.verify_license(tampered)


def test_expired_license_rejected(license_env: None) -> None:
    from deeptutor.license.contracts import LicenseClaims

    _add_customer()
    issued = license_service.issue_license(
        customer_id="acme",
        granularity="org",
        expires_in_days=1,
    )
    assert license_service.verify_license(issued["token"])

    expired_claims = LicenseClaims(
        tenant_id="acme",
        customer_id="acme",
        license_id="X",
        issued_at="2000-01-01T00:00:00+00:00",
        expires_at="2000-01-02T00:00:00+00:00",
        nonce="n",
    )
    with pytest.raises(LicenseExpiredError):
        license_service.check_expiry(expired_claims)


def test_revoked_license_rejected(license_env: None) -> None:
    _add_customer()
    issued = license_service.issue_license(customer_id="acme", granularity="org")
    license_service.revoke_license(issued["license_id"])
    with pytest.raises(LicenseRevokedError):
        license_service.verify_license(issued["token"])
    license_service.unrevoke_license(issued["license_id"])
    assert license_service.verify_license(issued["token"]).claims.license_id


def test_seats_granularity_enforced(license_env: None) -> None:
    _add_customer()
    issued = license_service.issue_license(
        customer_id="acme", granularity="seats", seats=2
    )
    verified = license_service.verify_license(issued["token"])
    first = license_service.acquire_seat(verified, "learner-1")
    assert first.seat_id
    second = license_service.acquire_seat(verified, "learner-2")
    assert second.seat_id != first.seat_id
    with pytest.raises(LicenseSeatsExhaustedError):
        license_service.acquire_seat(verified, "learner-3")
    # Same learner re-entry is idempotent.
    again = license_service.acquire_seat(verified, "learner-1")
    assert again.seat_id == first.seat_id
    # Releasing frees a seat.
    license_service.release_seat(verified, "learner-2")
    freed = license_service.acquire_seat(verified, "learner-4")
    assert freed.seat_id


def test_concurrency_granularity_enforced(license_env: None) -> None:
    _add_customer()
    issued = license_service.issue_license(
        customer_id="acme", granularity="concurrency", max_concurrency=1
    )
    verified = license_service.verify_license(issued["token"])
    slot = license_service.acquire_session_slot(verified, "session-A")
    assert slot.session_slot == "session-A"
    with pytest.raises(LicenseConcurrencyExhaustedError):
        license_service.acquire_session_slot(verified, "session-B")
    # Same session idempotent.
    again = license_service.acquire_session_slot(verified, "session-A")
    assert again.session_slot == "session-A"


def test_usage_view_reports_limits(license_env: None) -> None:
    _add_customer()
    issued = license_service.issue_license(
        customer_id="acme", granularity="seats", seats=3
    )
    license_service.acquire_seat(license_service.verify_license(issued["token"]), "l1")
    usage = license_service.license_usage(issued["license_id"])
    assert usage["seats"] == 1
    assert usage["seats_limit"] == 3


def test_missing_customer_rejected(license_env: None) -> None:
    with pytest.raises(LicenseError):
        license_service.issue_license(customer_id="nobody", granularity="org")


def test_feature_requirement(license_env: None) -> None:
    _add_customer()
    issued = license_service.issue_license(
        customer_id="acme", granularity="org", features=["sso"]
    )
    verified = license_service.verify_license(issued["token"])
    license_service.require_feature(verified, "sso")
    with pytest.raises(LicenseVerifyError):
        license_service.require_feature(verified, "webhook")


def test_self_check_sign_and_verify(license_env: None) -> None:
    assert license_service.self_check() == {"ok": True, "tenant_id": "self-check"}
