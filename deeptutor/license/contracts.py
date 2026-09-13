"""License token models and errors for the DeepTutor Enterprise license layer.

A license is a signed bearer token that encodes the entitlement granularity it
was issued at.  Three granularities are supported (KISS — they are the three
knobs a reseller or operator actually sells):

- ``org``      — one license covers an entire organization (tenant) on a
                 single private instance.  No seat/concurrency bookkeeping.
- ``seats``    — at most ``seats`` distinct learner accounts may be active.
- ``concurrency`` — at most ``concurrency`` simultaneously-active sessions.

The token carries a ``tenant_id`` claim, which is the same identifier the
multi-tenant isolation layer scopes records by, so one signed token both
proves entitlement and names the tenancy that must exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

LicenseGranularity = Literal["org", "seats", "concurrency"]

#: Granularity labels accepted by the issuer CLI / API.  Kept stable so a
#: license issued by an older build stays verifiable by a newer one.
LICENSE_GRANULARITIES: frozenset[str] = frozenset(
    {"org", "seats", "concurrency"}
)

#: Token prefix that mirrors the existing ``CLIPA-`` license key convention so
#: an operator recognizes an Enterprise license at a glance.
LICENSE_PREFIX = "DTLIC-"

LICENSE_FORMAT_VERSION = 1


class LicenseError(ValueError):
    """Base error for the license layer."""


class LicenseSignError(LicenseError):
    """A license could not be issued (missing/invalid issuer key)."""


class LicenseVerifyError(LicenseError):
    """A license token failed verification."""


class LicenseExpiredError(LicenseVerifyError):
    """The license token is cryptographically valid but past ``expires_at``."""


class LicenseRevokedError(LicenseVerifyError):
    """The license token is valid but appears on the revocation list."""


class LicenseSeatsExhaustedError(LicenseVerifyError):
    """The seats-granularity license has no free seat for this learner."""


class LicenseConcurrencyExhaustedError(LicenseVerifyError):
    """The concurrency-granularity license has no free session slot."""


@dataclass(frozen=True, slots=True)
class LicenseClaims:
    """Claims embedded inside a signed license token.

    Field naming mirrors the existing ``CLIPA`` license envelope
    (``format_version`` / ``customer_id`` / ``license_id`` / ``features`` /
    ``issued_at`` / ``expires_at`` / ``nonce``) so operators can compare the
    two formats side by side, plus the three granularity knobs this layer
    adds.
    """

    format_version: int = LICENSE_FORMAT_VERSION
    customer_id: str = ""
    tenant_id: str = ""
    license_id: str = ""
    features: list[str] = field(default_factory=list)
    granularity: LicenseGranularity = "org"
    seats: int = 0
    max_concurrency: int = 0
    issued_at: str = ""
    expires_at: str = ""
    nonce: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "format_version": self.format_version,
            "customer_id": self.customer_id,
            "tenant_id": self.tenant_id,
            "license_id": self.license_id,
            "features": list(self.features),
            "granularity": self.granularity,
            "seats": self.seats,
            "max_concurrency": self.max_concurrency,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "nonce": self.nonce,
        }


@dataclass(frozen=True, slots=True)
class VerifiedLicense:
    """A token that passed signature/expiry/revocation checks.

    ``seat_id`` / ``session_slot`` are filled in by the entitlement guards
    (``acquire_seat`` / ``acquire_session_slot``) and are otherwise empty.
    """

    claims: LicenseClaims
    raw_token: str
    seat_id: str = ""
    session_slot: str = ""
