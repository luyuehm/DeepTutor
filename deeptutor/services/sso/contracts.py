"""Models and errors for the DeepTutor SSO layer.

Both SSO protocols DeepTutor supports share the same outcome — an external
IdP asserts an identity, we map it to (or create) a local account, and the
normal session cookie is issued.  OIDC speaks JSON, SAML speaks SAML
Assertions; both are first verified cryptographically before any profile
field is trusted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SsoProtocol = Literal["oidc", "saml"]


class SsoError(ValueError):
    """Base error for the SSO layer."""


class SsoConfigError(SsoError):
    """The SSO provider is not configured or configured incorrectly."""


class SsoVerificationError(SsoError):
    """The IdP response failed cryptographic verification."""


class SsoStateError(SsoError):
    """The ``state`` / ``RelayState`` did not match what we sent."""


@dataclass(frozen=True, slots=True)
class SsoProfile:
    """The verified identity an IdP asserted, normalized across protocols.

    ``sub`` is the stable IdP subject id.  ``email`` / ``name`` may be empty
    if the provider did not assert them; the caller maps the profile to a
    local account (see :func:`deeptutor.services.sso.service.upsert_sso_user`).
    """

    protocol: SsoProtocol
    sub: str
    email: str = ""
    name: str = ""
    groups: list[str] = field(default_factory=list)

    def key(self) -> str:
        """A stable key for the issuer+subject that this profile names."""
        return f"{self.protocol}:{self.sub}"


@dataclass(frozen=True, slots=True)
class OidcProviderConfig:
    """Minimal OIDC provider config an operator wires up.

    ``issuer`` is the one value that must match the id token's ``iss`` exactly;
    ``client_id`` / ``client_secret`` are the confidential-client credentials.
    ``metadata_url`` may be used to fetch discovery, but the required claims
    below are the actual contract.
    """

    issuer: str
    client_id: str
    client_secret: str
    authorization_endpoint: str = ""
    token_endpoint: str = ""
    jwks_uri: str = ""
    redirect_uri: str = ""
    scopes: list[str] = field(default_factory=lambda: ["openid", "profile", "email"])


@dataclass(frozen=True, slots=True)
class SamlProviderConfig:
    """SAML IdP configuration.

    ``entity_id`` is the IdP entity id (``<saml:Issuer>`` value).  ``sso_url``
    is the HTTP-POST binding endpoint.  ``idp_cert_pem`` is the IdP's x509
    signing certificate, used to verify the Assertion signature.
    """

    entity_id: str
    sso_url: str
    idp_cert_pem: str
    audience: str = ""
    redirect_uri: str = ""


@dataclass(frozen=True, slots=True)
class SamlResponseEnvelope:
    """A parsed SAML Response before signature verification."""

    issuer: str
    status_code: str
    assertion: str  # raw <saml:Assertion> XML
    subject_name_id: str = ""
    audience: str = ""
    session_not_on_or_after: str = ""