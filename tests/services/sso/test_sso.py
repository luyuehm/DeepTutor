"""SSO integration tests.

These exercise the two protocol seams without hitting a real IdP:

- OIDC: build the authorization URL, verify the response_type/state contract,
  and verify state mismatch is rejected.  The full token-exchange + JWKS
  verification is exercised through a mocked httpx transport so no network
  call leaves the test process.
- SAML: the parser extracts subjects/audience, and the signature verifier is
  exercised with a **real** signed assertion produced with ``signxml`` so the
  important property (a wrong key is rejected) is pinned.
"""

from __future__ import annotations

import base64
import datetime
from typing import Any

import pytest

from deeptutor.services.sso import saml as saml_module
from deeptutor.services.sso import service as sso_service
from deeptutor.services.sso.contracts import (
    OidcProviderConfig,
    SamlProviderConfig,
    SsoStateError,
)


@pytest.fixture()
def oidc_provider() -> OidcProviderConfig:
    return OidcProviderConfig(
        issuer="https://idp.example.test",
        client_id="deep-tutor-app",
        client_secret="secret",
        authorization_endpoint="https://idp.example.test/oauth/authorize",
        token_endpoint="https://idp.example.test/oauth/token",
        jwks_uri="https://idp.example.test/oauth/jwks",
        redirect_uri="http://localhost:3000/api/auth/sso/oidc/callback",
        scopes=["openid", "profile", "email"],
    )


def test_oidc_authorization_url_contract(oidc_provider: OidcProviderConfig) -> None:
    state = sso_service.new_state()
    url = sso_service.build_oidc_authorization_url(oidc_provider, state, nonce="n1")
    assert url.startswith(oidc_provider.authorization_endpoint + "?")
    assert "response_type=code" in url
    assert f"client_id={oidc_provider.client_id}" in url
    assert f"state={state}" in url
    assert "nonce=n1" in url
    assert "response_type=token" not in url  # never the implicit flow


@pytest.mark.asyncio
async def test_oidc_state_mismatch_rejected(oidc_provider: OidcProviderConfig) -> None:
    with pytest.raises(SsoStateError):
        await sso_service.exchange_oidc_code(
            oidc_provider,
            code="some-code",
            redirect_uri=oidc_provider.redirect_uri,
            state="tampered",
            expected_state="the-real-state",
        )


@pytest.mark.asyncio
async def test_oidc_token_exchange_verifies_signature(oidc_provider: OidcProviderConfig) -> None:
    """A full code exchange against a mocked IdP: token endpoint + JWKS.

    The id token is signed with a test RSA key whose public half is served by
    the mocked JWKS; the issuer/audience claims name the configured values so
    verification must pass.
    """
    from datetime import timedelta, timezone

    from cryptography.hazmat.primitives.asymmetric import rsa
    import httpx
    from jose import jwt as jose_jwt

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "n": _b64(public_numbers.n),
        "e": _b64(public_numbers.e),
        "alg": "RS256",
        "use": "sig",
        "kid": "test-key-1",
    }

    now = datetime.datetime.now(timezone.utc)
    id_token = jose_jwt.encode(
        {
            "iss": oidc_provider.issuer,
            "sub": "idp-user-123",
            "aud": oidc_provider.client_id,
            "email": "alice@example.test",
            "name": "Alice",
            "exp": now + timedelta(hours=1),
            "iat": now,
        },
        key,
        algorithm="RS256",
        headers={"kid": "test-key-1"},
    )

    async def _fake_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(
                200,
                json={"id_token": id_token, "access_token": "at", "token_type": "Bearer"},
            )
        if request.url.path.endswith("/jwks"):
            return httpx.Response(200, json={"keys": [jwk]})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(_fake_handler))
    try:
        profile = await sso_service.exchange_oidc_code(
            oidc_provider,
            code="code-1",
            redirect_uri=oidc_provider.redirect_uri,
            state="state-1",
            expected_state="state-1",
            http_client=client,
        )
    finally:
        await client.aclose()
    assert profile.protocol == "oidc"
    assert profile.sub == "idp-user-123"
    assert profile.email == "alice@example.test"


def _b64(value: int) -> str:
    length = (value.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode()


def test_saml_parser_extracts_subject_and_audience() -> None:
    parsed = saml_module.parse_saml_response(_base64_saml_response())
    assert parsed.subject_name_id == "saml-user-1"
    assert parsed.audience == "https://sp.example.test"
    assert parsed.issuer == "https://idp.example.test/saml"


def test_saml_signature_rejects_wrong_key() -> None:
    """A signed assertion must fail verification under the wrong certificate."""
    raw, cert_pem = _build_signed_assertion()
    provider = SamlProviderConfig(
        entity_id="https://idp.example.test/saml",
        sso_url="https://idp.example.test/saml/sso",
        idp_cert_pem=cert_pem,
        audience="https://sp.example.test",
    )
    # The correct key must pass.
    result = saml_module.verify_saml_response(provider, raw)
    assert result["sub"] == "saml-user-1"

    # A *different* certificate than the one that signed must fail.
    _, wrong_cert = _self_signed_cert("other-idp")
    wrong_provider = SamlProviderConfig(
        entity_id="https://idp.example.test/saml",
        sso_url="https://idp.example.test/saml/sso",
        idp_cert_pem=wrong_cert,
        audience="https://sp.example.test",
    )
    with pytest.raises(Exception):
        saml_module.verify_saml_response(wrong_provider, raw)


def _base64_saml_response() -> str:
    return base64.b64encode(SAML_RESPONSE_TEMPLATE.encode("utf-8")).decode()


def _self_signed_cert(cn: str) -> tuple[Any, str]:
    """Return (private_key, cert PEM).  The key signs, the PEM is verified."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return key, cert.public_bytes(serialization.Encoding.PEM).decode()


def _build_signed_assertion() -> tuple[str, str]:
    """Return a (base64 SAMLResponse, cert PEM) whose Assertion is XML-signed."""
    from cryptography.hazmat.primitives.serialization import Encoding
    from lxml import etree
    from signxml import XMLSigner, methods

    key, cert_pem = _self_signed_cert("idp.example.test")
    root = etree.fromstring(SAML_RESPONSE_TEMPLATE.encode("utf-8"))
    signer = XMLSigner(
        method=methods.enveloped,
        signature_algorithm="rsa-sha256",
        c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#",
    )
    signed = signer.sign(root, key=key, cert=cert_pem)
    raw_xml = etree.tostring(signed, encoding="utf-8")
    return base64.b64encode(raw_xml).decode(), cert_pem


SAML_RESPONSE_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<samlp:Response
    xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
    xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
    xmlns:ds="http://www.w3.org/2000/09/xmldsig#"
    ID="_r1" InResponseTo="_req" Version="2.0"
    IssueInstant="2026-09-13T00:00:00Z"
    Destination="https://sp.example.test/acs">
  <saml:Issuer>https://idp.example.test/saml</saml:Issuer>
  <samlp:Status>
    <samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/>
  </samlp:Status>
  <saml:Assertion ID="_a1" Version="2.0" IssueInstant="2026-09-13T00:00:00Z">
    <saml:Issuer>https://idp.example.test/saml</saml:Issuer>
    <saml:Subject>
      <saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified">saml-user-1</saml:NameID>
      <saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">
        <saml:SubjectConfirmationData Recipient="https://sp.example.test/acs" NotOnOrAfter="2099-01-01T00:00:00Z"/>
      </saml:SubjectConfirmation>
    </saml:Subject>
    <saml:Conditions NotBefore="2026-09-13T00:00:00Z" NotOnOrAfter="2099-01-01T00:00:00Z">
      <saml:AudienceRestriction>
        <saml:Audience>https://sp.example.test</saml:Audience>
      </saml:AudienceRestriction>
    </saml:Conditions>
    <saml:AuthnStatement AuthnInstant="2026-09-13T00:00:00Z">
      <saml:AuthnContext>
        <saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport</saml:AuthnContextClassRef>
      </saml:AuthnContext>
    </saml:AuthnStatement>
  </saml:Assertion>
</samlp:Response>
"""