"""Issuing, verifying, and enforcing DeepTutor Enterprise licenses.

Design mirrors the existing ``CLIPA`` offline license paradigm (a signed
bearer token whose payload is readable after the signature) so an operator who
already runs the CLIProxy license flow recognizes the shape, while keeping the
assets completely local — no license server, no internet round-trip.

The signing key may come from one of two places:

1. The process environment variable ``DEEPTUTOR_LICENSE_PRIVATE_KEY`` (hex,
   at least 32 bytes when decoded) — the deployment-injected secret.
2. A stable file ``data/system/license/issuer_key`` created on first use with
   a warning, exactly like ``auth_secret`` does today.

An issuer key is required to *sign* new licenses and to *verify* the built-in
self-check; a deployment that only validates externally-issued licenses must
still configure the same key that signed them.  Either way, the key never
appears in logs or in any persisted license payload.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import os
import secrets
import threading
import time
from typing import Any

from jose import JWTError, jwt

from deeptutor.multi_user import paths as mu_paths

from .contracts import (
    LICENSE_FORMAT_VERSION,
    LICENSE_GRANULARITIES,
    LicenseClaims,
    LicenseConcurrencyExhaustedError,
    LicenseError,
    LicenseExpiredError,
    LicenseRevokedError,
    LicenseSeatsExhaustedError,
    LicenseSignError,
    LicenseVerifyError,
    VerifiedLicense,
)
from .store import (  # noqa: F401  (re-exported: the service module IS the public license API)
    get_customer,
    get_issued_license,
    list_issued_licenses,
    load_customers,
    load_license_state,
    load_revoked,
    persist_entitlement_state,
    revoke_license,
    save_customer,
    save_issued_license,
    unrevoke_license,
)

logger = logging.getLogger(__name__)

#: Env var that injects the signing key.  Hex-encoded; decoded length must be
#: at least ``_MIN_KEY_BYTES``.
ENV_LICENSE_PRIVATE_KEY = "DEEPTUTOR_LICENSE_PRIVATE_KEY"

_MIN_KEY_BYTES = 32
_ALGORITHM = "HS256"

#: Filesystem mode of a freshly generated issuer key (owner-only).
_ISSUER_KEY_MODE = 0o600

#: Seat/session names are nonce-ish and must satisfy the same charset as the
#: file-oriented id paths on disk.
_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"

#: How long an entitlement lease is held before a re-validate can reclaim it.
_SEAT_LEASE_SECONDS = 4 * 60 * 60
_SESSION_LEASE_SECONDS = 12 * 60 * 60

_entitlement_lock = threading.RLock()


# ---------------------------------------------------------------------------
# Issuer key
# ---------------------------------------------------------------------------


def _issuer_key_path() -> Any:
    root = mu_paths.SYSTEM_ROOT / "license"
    root.mkdir(parents=True, exist_ok=True)
    return root / "issuer_key"


def load_or_create_issuer_key() -> str:
    """Return the configured signing key, creating a stable local one on first use."""
    from_env = os.getenv(ENV_LICENSE_PRIVATE_KEY, "").strip()
    if from_env:
        if not _valid_key_material(from_env):
            raise LicenseSignError(
                f"{ENV_LICENSE_PRIVATE_KEY} must be at least {_MIN_KEY_BYTES} "
                "bytes of hex entropy"
            )
        return from_env
    path = _issuer_key_path()
    try:
        if path.exists():
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                _check_key_strength(existing)
                return existing
        generated = secrets.token_hex(_MIN_KEY_BYTES)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        descriptor = os.open(path, flags, _ISSUER_KEY_MODE)
        try:
            os.write(descriptor, generated.encode("ascii"))
        finally:
            os.close(descriptor)
        logger.warning(
            "No DEEPTUTOR_LICENSE_PRIVATE_KEY configured; generated a stable "
            "local issuer key at %s",
            path,
        )
        return generated
    except OSError as exc:
        logger.warning("Could not persist issuer key at %s: %s", path, exc)
        raise LicenseSignError(f"cannot create issuer key: {exc}") from exc


def _valid_key_material(key: str) -> bool:
    try:
        return len(bytes.fromhex(key)) >= _MIN_KEY_BYTES
    except ValueError:
        return False


def _check_key_strength(key: str) -> None:
    if not _valid_key_material(key):
        raise LicenseSignError("issuer key file holds insufficient entropy")


# ---------------------------------------------------------------------------
# Issuing
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_license_id() -> str:
    return f"LIC-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"


def issue_license(
    *,
    customer_id: str,
    tenant_id: str = "",
    granularity: str = "org",
    seats: int = 0,
    max_concurrency: int = 0,
    features: list[str] | None = None,
    expires_in_days: int = 365,
    issuer_key: str | None = None,
) -> dict[str, str]:
    """Issue a signed license token and record it in the issuer ledger.

    ``customer_id`` must reference an existing customer registry record
    (``save_customer``).  ``tenant_id`` defaults to ``customer_id`` so the
    single-tenant case is one less thing to configure, while a reseller who
    hosts several operators on one instance issues one license per tenant.
    """
    customer_record = get_customer(customer_id)
    if customer_record is None:
        raise LicenseError(f"unknown customer: {customer_id}")

    granularity = str(granularity or "").strip().lower()
    if granularity not in LICENSE_GRANULARITIES:
        raise LicenseError(
            f"unsupported granularity {granularity!r}; choose from "
            + ", ".join(sorted(LICENSE_GRANULARITIES))
        )
    seats = max(0, int(seats or 0))
    max_concurrency = max(0, int(max_concurrency or 0))
    if granularity == "seats" and seats <= 0:
        raise LicenseError("seats-granularity licenses require seats > 0")
    if granularity == "concurrency" and max_concurrency <= 0:
        raise LicenseError("concurrency-granularity licenses require max_concurrency > 0")
    if expires_in_days <= 0:
        raise LicenseError("expires_in_days must be positive")

    now = datetime.now(timezone.utc)
    claims = LicenseClaims(
        customer_id=customer_id,
        tenant_id=str(tenant_id or customer_id),
        license_id=_new_license_id(),
        features=[str(feature).strip() for feature in (features or []) if str(feature).strip()],
        granularity=granularity,  # type: ignore[arg-type]
        seats=seats,
        max_concurrency=max_concurrency,
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(days=expires_in_days)).isoformat(),
        nonce=secrets.token_hex(8),
    )

    secret = issuer_key or load_or_create_issuer_key()
    token = _sign_claims(claims, secret)
    save_issued_license(
        {
            "license_id": claims.license_id,
            "tenant_id": claims.tenant_id,
            "customer_id": claims.customer_id,
            "granularity": claims.granularity,
            "features": list(claims.features),
            "seats": claims.seats,
            "max_concurrency": claims.max_concurrency,
            "token": token,
            "issued_at": claims.issued_at,
            "expires_at": claims.expires_at,
        }
    )
    return {
        "license_id": claims.license_id,
        "token": token,
        "tenant_id": claims.tenant_id,
        "granularity": claims.granularity,
        "expires_at": claims.expires_at,
    }


def _sign_claims(claims: LicenseClaims, secret: str) -> str:
    try:
        return jwt.encode(claims.to_dict(), secret, algorithm=_ALGORITHM)
    except Exception as exc:
        raise LicenseSignError(f"could not sign license: {exc}") from exc


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_license(
    token: str,
    *,
    issuer_key: str | None = None,
    features_required: list[str] | None = None,
    check_revocation: bool = True,
    tenant_id: str | None = None,
) -> VerifiedLicense:
    """Verify a license token and enforce time/revocation/feature gates.

    Returns a :class:`VerifiedLicense` on success and raises a
    :class:`LicenseVerifyError` subclass on every failure mode.  ``tenant_id``
    pins verification to one tenancy — the multi-tenant isolation layer passes
    the request tenant here so a license issued for another operator's tenancy
    cannot validate on this one.
    """
    token = str(token or "").strip()
    if not token:
        raise LicenseVerifyError("empty license token")

    secret = issuer_key or load_or_create_issuer_key()
    try:
        payload = jwt.decode(token, secret, algorithms=[_ALGORITHM])
    except JWTError as exc:
        raise LicenseVerifyError(f"license signature invalid: {exc}") from exc
    if not isinstance(payload, dict):
        raise LicenseVerifyError("license payload is not an object")

    claims = _claims_from_payload(payload)
    issued = _parse_iso(claims.issued_at)
    expires = _parse_iso(claims.expires_at)
    now = datetime.now(timezone.utc)
    if issued and expires and now.timestamp() > expires.timestamp():
        raise LicenseExpiredError(f"license expired at {claims.expires_at}")

    if tenant_id is not None and claims.tenant_id and str(claims.tenant_id) != str(tenant_id):
        raise LicenseVerifyError(
            f"license tenant {claims.tenant_id!r} != requested tenant {tenant_id!r}"
        )

    if check_revocation and claims.license_id in load_revoked():
        raise LicenseRevokedError(f"license revoked: {claims.license_id}")

    if features_required:
        missing = [
            feature
            for feature in features_required
            if feature not in claims.features
        ]
        if missing:
            raise LicenseVerifyError(f"license lacks features: {missing}")

    return VerifiedLicense(claims=claims, raw_token=token)


def _claims_from_payload(payload: dict[str, Any]) -> LicenseClaims:
    granularity = str(payload.get("granularity") or "org").strip().lower()
    if granularity not in LICENSE_GRANULARITIES:
        granularity = "org"
    return LicenseClaims(
        format_version=int(payload.get("format_version") or LICENSE_FORMAT_VERSION),
        customer_id=str(payload.get("customer_id") or ""),
        tenant_id=str(payload.get("tenant_id") or payload.get("customer_id") or ""),
        license_id=str(payload.get("license_id") or ""),
        features=[str(f) for f in (payload.get("features") or []) if isinstance(f, str)],
        granularity=granularity,  # type: ignore[arg-type]
        seats=max(0, int(payload.get("seats") or 0)),
        max_concurrency=max(0, int(payload.get("max_concurrency") or 0)),
        issued_at=str(payload.get("issued_at") or ""),
        expires_at=str(payload.get("expires_at") or ""),
        nonce=str(payload.get("nonce") or ""),
    )


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def check_expiry(claims: LicenseClaims) -> None:
    """Raise :class:`LicenseExpiredError` when the license is past ``expires_at``."""
    expires = _parse_iso(claims.expires_at)
    if expires and datetime.now(timezone.utc).timestamp() > expires.timestamp():
        raise LicenseExpiredError(f"license expired at {claims.expires_at}")


# ---------------------------------------------------------------------------
# Entitlement guards — seats and concurrency
# ---------------------------------------------------------------------------


def acquire_seat(
    verified: VerifiedLicense,
    learner_id: str,
    *,
    reacquire: bool = False,
    issuer_key: str | None = None,
) -> VerifiedLicense:
    """Lease one seat of a ``seats``-granularity license to *learner_id*.

    Idempotent per learner: a learner who already holds a lease keeps it
    (re-entering a course does not consume a second seat).  Raises
    :class:`LicenseSeatsExhaustedError` when every seat is taken by someone
    else.  ``reacquire`` allows a known learner to renew their lease, which
    the per-request guard uses for an authenticated recurring user.
    """
    _check_granularity(verified, "seats")
    learner_id = str(learner_id or "").strip()
    if not learner_id:
        raise LicenseError("seat acquisition requires a learner id")

    with _entitlement_lock:
        state = load_license_state()
        license_state = state.setdefault("license_state", {}).setdefault(
            verified.claims.license_id,
            {"seats": {}, "sessions": {}},
        )
        seats = license_state.setdefault("seats", {})

        now = time.time()
        leaser = _lease_holder(verified.claims.license_id, learner_id, state=state)
        if leaser is not None:
            if reacquire:
                seats[leaser]["assigned_at"] = _now_iso()
                persists = True
            else:
                persists = False
            seat_id = leaser
        else:
            _expire_stale_seats(seats, now)
            if len(seats) >= verified.claims.seats:
                raise LicenseSeatsExhaustedError(
                    f"all {verified.claims.seats} seats are taken"
                )
            seat_id = f"seat_{secrets.token_hex(4)}"
            seats[seat_id] = {
                "learner_id": learner_id,
                "assigned_at": _now_iso(),
                "lease_until": now + _SEAT_LEASE_SECONDS,
            }
            persists = True

        if persists:
            persist_entitlement_state(state)

    return VerifiedLicense(
        claims=verified.claims,
        raw_token=verified.raw_token,
        seat_id=seat_id,
        session_slot="",
    )


def release_seat(verified: VerifiedLicense, learner_id: str) -> None:
    """Release *learner_id*'s seat on this license (no-op when not held)."""
    with _entitlement_lock:
        state = load_license_state()
        license_state = state.get("license_state", {}).get(verified.claims.license_id, {})
        seats = license_state.get("seats", {})
        for seat_id, seat in list(seats.items()):
            if str(seat.get("learner_id") or "") == str(learner_id):
                seats.pop(seat_id, None)
                persist_entitlement_state(state)
                break


def acquire_session_slot(
    verified: VerifiedLicense,
    session_id: str,
    learner_id: str = "",
    *,
    max_concurrency: int = 0,
) -> VerifiedLicense:
    """Lease one concurrency slot for *session_id* (idempotent per session).

    A second concurrent ``acquire_session_slot`` for the same session id is a
    no-op (the same learner reconnecting), while a new session on a full
    license raises :class:`LicenseConcurrencyExhaustedError`.
    """
    limit = max_concurrency or verified.claims.max_concurrency
    if limit <= 0:
        raise LicenseError("concurrency license requires max_concurrency > 0")

    with _entitlement_lock:
        state = load_license_state()
        license_state = state.setdefault("license_state", {}).setdefault(
            verified.claims.license_id,
            {"seats": {}, "sessions": {}},
        )
        sessions = license_state.setdefault("sessions", {})

        session_id = str(session_id or "")
        existing = sessions.get(session_id)
        if existing is not None:
            existing["started_at"] = _now_iso()
            persist_entitlement_state(state)
            return VerifiedLicense(
                claims=verified.claims,
                raw_token=verified.raw_token,
                session_slot=session_id,
                seat_id="",
            )

        now = time.time()
        _expire_stale_sessions(sessions, now)
        if len(sessions) >= limit:
            raise LicenseConcurrencyExhaustedError(
                f"all {limit} concurrency slots are busy"
            )
        sessions[session_id] = {
            "learner_id": str(learner_id or ""),
            "started_at": _now_iso(),
            "lease_until": now + _SESSION_LEASE_SECONDS,
        }
        persist_entitlement_state(state)

    return VerifiedLicense(
        claims=verified.claims,
        raw_token=verified.raw_token,
        seat_id="",
        session_slot=session_id,
    )


def release_session_slot(verified: VerifiedLicense, session_id: str) -> None:
    with _entitlement_lock:
        state = load_license_state()
        sessions = state.get("license_state", {}).get(verified.claims.license_id, {}).get(
            "sessions",
            {},
        )
        if session_id in sessions:
            sessions.pop(session_id, None)
            persist_entitlement_state(state)


def _check_granularity(verified: VerifiedLicense, expected: str) -> None:
    if verified.claims.granularity != expected:
        raise LicenseError(
            f"license is {verified.claims.granularity}-granularity, not {expected}"
        )


def _lease_holder(license_id: str, learner_id: str, *, state: dict[str, Any]) -> str | None:
    """The seat id already held by *learner_id*, or None."""
    license_state = state.get("license_state", {}).get(license_id, {})
    for seat_id, seat in license_state.get("seats", {}).items():
        if str(seat.get("learner_id") or "") == str(learner_id):
            return str(seat_id)
    return None


def _expire_stale_seats(seats: dict[str, Any], now: float) -> None:
    for seat_id in list(seats.keys()):
        until = float(seats[seat_id].get("lease_until") or 0)
        if until and now > until:
            seats.pop(seat_id, None)


def _expire_stale_sessions(sessions: dict[str, Any], now: float) -> None:
    for session_id in list(sessions.keys()):
        until = float(sessions[session_id].get("lease_until") or 0)
        if until and now > until:
            sessions.pop(session_id, None)


# ---------------------------------------------------------------------------
# Admin helpers
# ---------------------------------------------------------------------------


def list_customers() -> list[dict[str, Any]]:
    customers = load_customers()
    return sorted(customers.values(), key=lambda record: str(record.get("created_at") or ""))


def license_usage(license_id: str) -> dict[str, Any]:
    """A compact usage view an admin console can render."""
    state = load_license_state()
    license_state = state.get("license_state", {}).get(license_id, {})
    seats = license_state.get("seats", {})
    sessions = license_state.get("sessions", {})
    record = get_issued_license(license_id) or {}
    return {
        "license_id": license_id,
        "seats": len(seats),
        "seats_limit": int(record.get("seats") or 0),
        "active_sessions": len(sessions),
        "concurrency_limit": int(record.get("max_concurrency") or 0),
    }


# ---------------------------------------------------------------------------
# Feature-gate helper for the three-tier pricing gate (RIC-718)
# ---------------------------------------------------------------------------


def require_feature(verified: VerifiedLicense, feature: str) -> None:
    """Raise when the verified license does not carry *feature*.

    Used by the tier-gate seam (community / team / enterprise) so a single
    signed license toggles the same feature flags an operator configures
    today.  ``import`` must stay local — this module cannot import the router
    layer.
    """
    if feature not in verified.claims.features:
        raise LicenseVerifyError(f"license lacks feature {feature!r}")


# ---------------------------------------------------------------------------
# Self-check (used by the API's health/verify endpoint)
# ---------------------------------------------------------------------------


def self_check(issuer_key: str | None = None) -> dict[str, Any]:
    """Sign a throwaway license for the deployment itself and verify it back.

    Proves the configured key can both sign and verify — the failure mode that
    would otherwise only surface at a customer install.
    """
    key = issuer_key or load_or_create_issuer_key()
    from .contracts import LicenseClaims as _Claims

    claims = _Claims(
        customer_id="self-check",
        tenant_id="self-check",
        license_id="SELF-CHECK",
        features=[],
        issued_at=_now_iso(),
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        nonce=secrets.token_hex(8),
    )
    token = _sign_claims(claims, key)
    verified = verify_license(token, issuer_key=key)
    return {"ok": True, "tenant_id": verified.claims.tenant_id}