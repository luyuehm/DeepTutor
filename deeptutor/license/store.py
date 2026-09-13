"""In-memory and file-backed store for the DeepTutor Enterprise license layer.

Persistence follows the payment subsystem's convention — JSON files under
``<runtime-home>/data/system/license``:

``data/system/license/customers.json``
    Customer (tenant) registry: id, name, contact, license_type, status,
    activated_at, expires_at.  This doubles as the list of tenants the
    multi-tenant layer may scope records by when ``require_tenant`` is on.

``data/system/license/licenses.json``
    Issuer-side ledger of every issued license key: the full token (the key
    itself is the bearer credential, so the ledger stores the token so a
    support run can re-derive a key for a customer), plus issue metadata.

``data/system/license/revoked.json``
    Ordered list of ``license_id`` values revoked by an operator.  A revoked
    license keeps failing verification even though its signature is valid.

``data/system/license/state.json``
    Runtime entitlement state for the ``seats`` / ``concurrency``
    granularities: which learner account holds which seat and which sessions
    currently hold concurrency slots.

All files are written atomically (same-directory temp + rename) and guarded
by an in-process write lock so concurrent web requests cannot double-issue a
seat or double-assign a concurrency slot.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import threading
from typing import Any

from deeptutor.multi_user import paths as mu_paths
from deeptutor.services.file_io import atomic_write_json

logger = logging.getLogger(__name__)

#: Directory name under ``data/system``.
LICENSE_DIRNAME = "license"

CUSTOMERS_FILE_NAME = "customers.json"
LICENSES_FILE_NAME = "licenses.json"
REVOKED_FILE_NAME = "revoked.json"
STATE_FILE_NAME = "state.json"

_WRITE_LOCK = threading.RLock()


def license_root() -> Path:
    """The file-store root for the license layer, resolved per call.

    Resolved through ``mu_paths`` so tests that monkeypatch
    ``paths.SYSTEM_ROOT`` take effect without a module reload (see the
    multi-user test suite).
    """
    mu_paths.ensure_system_dirs()
    root = mu_paths.SYSTEM_ROOT / LICENSE_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def customers_path() -> Path:
    return license_root() / CUSTOMERS_FILE_NAME


def licenses_path() -> Path:
    return license_root() / LICENSES_FILE_NAME


def revoked_path() -> Path:
    return license_root() / REVOKED_FILE_NAME


def state_path() -> Path:
    return license_root() / STATE_FILE_NAME


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("Unreadable license store at %s: %s", path, exc)
        return fallback
    return loaded if isinstance(loaded, type(fallback)) else fallback


# ---------------------------------------------------------------------------
# Customer (tenant) registry
# ---------------------------------------------------------------------------


def _empty_customer_payload() -> dict[str, Any]:
    return {"version": 1, "customers": {}}


def load_customers() -> dict[str, dict[str, Any]]:
    payload = _read_json(customers_path(), _empty_customer_payload())
    raw = payload.get("customers") if isinstance(payload, dict) else None
    return raw if isinstance(raw, dict) else {}


def save_customer(record: dict[str, Any]) -> None:
    customer_id = str(record.get("id") or "")
    if not customer_id:
        raise ValueError("customer id is required")
    with _WRITE_LOCK:
        payload = _read_json(customers_path(), _empty_customer_payload())
        if not isinstance(payload, dict):
            payload = _empty_customer_payload()
        customers = payload.setdefault("customers", {})
        customers[customer_id] = record
        atomic_write_json(customers_path(), payload)


def get_customer(customer_id: str) -> dict[str, Any] | None:
    return load_customers().get(customer_id)


# ---------------------------------------------------------------------------
# Issuer-side license ledger
# ---------------------------------------------------------------------------


def _empty_license_payload() -> dict[str, Any]:
    return {"version": 1, "licenses": {}}


def load_issued_licenses() -> dict[str, dict[str, Any]]:
    payload = _read_json(licenses_path(), _empty_license_payload())
    raw = payload.get("licenses") if isinstance(payload, dict) else None
    return raw if isinstance(raw, dict) else {}


def save_issued_license(record: dict[str, Any]) -> None:
    license_id = str(record.get("license_id") or "")
    if not license_id:
        raise ValueError("license_id is required")
    with _WRITE_LOCK:
        payload = _read_json(licenses_path(), _empty_license_payload())
        if not isinstance(payload, dict):
            payload = _empty_license_payload()
        payload.setdefault("licenses", {})[license_id] = record
        atomic_write_json(licenses_path(), payload)


def get_issued_license(license_id: str) -> dict[str, Any] | None:
    return load_issued_licenses().get(license_id)


def list_issued_licenses() -> list[dict[str, Any]]:
    return sorted(
        load_issued_licenses().values(),
        key=lambda record: str(record.get("issued_at") or ""),
    )


# ---------------------------------------------------------------------------
# Revocation list
# ---------------------------------------------------------------------------


def _empty_revoked_payload() -> dict[str, Any]:
    return {"version": 1, "revoked": []}


def load_revoked() -> list[str]:
    payload = _read_json(revoked_path(), _empty_revoked_payload())
    raw = payload.get("revoked") if isinstance(payload, dict) else None
    return [str(item) for item in raw] if isinstance(raw, list) else []


def revoke_license(license_id: str) -> None:
    license_id = str(license_id or "").strip()
    if not license_id:
        raise ValueError("license_id is required")
    with _WRITE_LOCK:
        payload = _read_json(revoked_path(), _empty_revoked_payload())
        if not isinstance(payload, dict):
            payload = _empty_revoked_payload()
        revoked = [str(item) for item in payload.get("revoked", []) if isinstance(item, str)]
        if license_id not in revoked:
            revoked.append(license_id)
        payload["revoked"] = revoked
        atomic_write_json(revoked_path(), payload)


def unrevoke_license(license_id: str) -> None:
    license_id = str(license_id or "").strip()
    with _WRITE_LOCK:
        payload = _read_json(revoked_path(), _empty_revoked_payload())
        if not isinstance(payload, dict):
            payload = _empty_revoked_payload()
        payload["revoked"] = [
            item for item in payload.get("revoked", []) if str(item) != license_id
        ]
        atomic_write_json(revoked_path(), payload)


# ---------------------------------------------------------------------------
# Runtime entitlement state (seats / concurrency)
# ---------------------------------------------------------------------------


def _empty_state_payload() -> dict[str, Any]:
    return {
        "version": 1,
        # license_id -> {"seats": {seat_id: {"learner_id": ... ,"assigned_at": ...}},
        #                "sessions": {session_slot: {"learner_id": ..., "started_at": ...}}}
        "license_state": {},
    }


def load_license_state() -> dict[str, Any]:
    payload = _read_json(state_path(), _empty_state_payload())
    if not isinstance(payload, dict):
        return _empty_state_payload()
    payload.setdefault("version", 1)
    payload.setdefault("license_state", {})
    return payload


def _save_license_state(payload: dict[str, Any]) -> None:
    atomic_write_json(state_path(), payload)


def lease_entitlement_state() -> dict[str, Any]:
    """Return the license-state payload under the module write lock."""
    state = load_license_state()
    return state


def persist_entitlement_state(state: dict[str, Any]) -> None:
    with _WRITE_LOCK:
        _save_license_state(state)