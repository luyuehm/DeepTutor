"""Admin APIs for the DeepTutor Enterprise license layer.

Admin-facing (``Depends(require_admin)``):
    GET    /api/license/customers
    POST   /api/license/customers
    GET    /api/license/licenses
    POST   /api/license/licenses/issue
    POST   /api/license/licenses/{license_id}/revoke
    POST   /api/license/licenses/{license_id}/unrevoke
    GET    /api/license/licenses/{license_id}/usage
    GET    /api/license/self-check

Operator-facing (``Depends(require_auth)`` — any authenticated learner can
verify their own instance's license):
    POST   /api/license/verify
    POST   /api/license/acquire-seat
    POST   /api/license/release-seat

The issuer-key secret never crosses the wire: every signing operation reads it
from the deployment environment or the stable local key file.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from deeptutor.api.routers.auth import require_admin
from deeptutor.license import service as license_service
from deeptutor.license.contracts import (
    LicenseError,
    LicenseVerifyError,
)

router = APIRouter()


class CustomerPayload(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=120)
    contact: str = Field(default="", max_length=120)
    notes: str = Field(default="", max_length=400)


class IssueLicensePayload(BaseModel):
    customer_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default="", max_length=64)
    granularity: str = Field(default="org", pattern="^(org|seats|concurrency)$")
    seats: int = Field(default=0, ge=0)
    max_concurrency: int = Field(default=0, ge=0)
    features: list[str] = Field(default_factory=list)
    expires_in_days: int = Field(default=365, ge=1, le=3650)


class VerifyPayload(BaseModel):
    token: str = Field(min_length=8, max_length=4096)
    tenant_id: str = Field(default="", max_length=64)
    features_required: list[str] = Field(default_factory=list)


class AcquireSeatPayload(BaseModel):
    token: str = Field(min_length=8, max_length=4096)
    learner_id: str = Field(min_length=1, max_length=64)
    reacquire: bool = False


class ReleaseSeatPayload(BaseModel):
    token: str = Field(min_length=8, max_length=4096)
    learner_id: str = Field(min_length=1, max_length=64)


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, LicenseVerifyError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, LicenseError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail="license operation failed")


@router.get("/customers", tags=["license"])
async def list_customers(_: Any = Depends(require_admin)) -> dict[str, Any]:
    return {"customers": license_service.list_customers()}


@router.post("/customers", status_code=201, tags=["license"])
async def create_customer(payload: CustomerPayload) -> dict[str, Any]:
    record = payload.model_dump()
    record["status"] = "active"
    record["created_at"] = datetime.now(timezone.utc).isoformat()
    license_service.save_customer(record)
    return {"customer": record}


@router.get("/licenses", tags=["license"])
async def list_licenses(_: Any = Depends(require_admin)) -> dict[str, Any]:
    licenses = license_service.list_issued_licenses()
    return {
        "licenses": [
            {
                "license_id": record.get("license_id"),
                "tenant_id": record.get("tenant_id"),
                "customer_id": record.get("customer_id"),
                "granularity": record.get("granularity"),
                "features": record.get("features"),
                "seats": record.get("seats"),
                "max_concurrency": record.get("max_concurrency"),
                "issued_at": record.get("issued_at"),
                "expires_at": record.get("expires_at"),
            }
            for record in licenses
        ]
    }


@router.post("/licenses/issue", tags=["license"])
async def issue_license(
    payload: IssueLicensePayload, _: Any = Depends(require_admin)
) -> dict[str, Any]:
    try:
        issued = license_service.issue_license(**payload.model_dump())
    except LicenseError as exc:
        raise _error(exc) from exc
    return issued


@router.post("/licenses/{license_id}/revoke", tags=["license"])
async def revoke_license(
    license_id: str, _: Any = Depends(require_admin)
) -> dict[str, Any]:
    license_service.revoke_license(license_id)
    return {"ok": True, "license_id": license_id}


@router.post("/licenses/{license_id}/unrevoke", tags=["license"])
async def unrevoke_license(
    license_id: str, _: Any = Depends(require_admin)
) -> dict[str, Any]:
    license_service.unrevoke_license(license_id)
    return {"ok": True, "license_id": license_id}


@router.get("/licenses/{license_id}/usage", tags=["license"])
async def license_usage(
    license_id: str, _: Any = Depends(require_admin)
) -> dict[str, Any]:
    return license_service.license_usage(license_id)


@router.get("/self-check", tags=["license"])
async def self_check(_: Any = Depends(require_admin)) -> dict[str, Any]:
    return license_service.self_check()


# ---------------------------------------------------------------------------
# Learner/operator-facing verification & entitlement
# ---------------------------------------------------------------------------


@router.post("/verify", tags=["license"])
async def verify(payload: VerifyPayload) -> dict[str, Any]:
    try:
        verified = license_service.verify_license(
            payload.token,
            tenant_id=payload.tenant_id or None,
            features_required=payload.features_required or None,
        )
    except LicenseVerifyError as exc:
        raise _error(exc) from exc
    return {
        "ok": True,
        "tenant_id": verified.claims.tenant_id,
        "granularity": verified.claims.granularity,
        "license_id": verified.claims.license_id,
        "features": verified.claims.features,
        "expires_at": verified.claims.expires_at,
    }


@router.post("/acquire-seat", tags=["license"])
async def acquire_seat(payload: AcquireSeatPayload) -> dict[str, Any]:
    try:
        verified = license_service.verify_license(payload.token)
        seat = license_service.acquire_seat(
            verified, payload.learner_id, reacquire=payload.reacquire
        )
    except LicenseError as exc:
        raise _error(exc) from exc
    return {"ok": True, "seat_id": seat.seat_id}


@router.post("/release-seat", tags=["license"])
async def release_seat(payload: ReleaseSeatPayload) -> dict[str, Any]:
    try:
        verified = license_service.verify_license(payload.token)
        license_service.release_seat(verified, payload.learner_id)
    except LicenseError as exc:
        raise _error(exc) from exc
    return {"ok": True}
