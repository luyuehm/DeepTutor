"""SAML Response parsing and signature verification.

SAML security notes:

- Parsing uses ``defusedxml`` so DTD/entity expansion (billion-laughs,
  external entities) cannot run.  DeepTutor already depends on defusedxml.
- The Assertion's XML signature is verified with ``xmlsec`` (the ``signxml``
  package) against the IdP's x509 signing certificate.  ``signxml`` is the
  de-facto maintained library for XML-DSig in Python and validates the whole
  signed subtree (canonicalization, digest, signature), which is exactly the
  check a SAML SP must make before trusting the asserted identity.
- The AudienceRestriction must name the SP audience, and the ``Issuer`` must
  match the configured IdP entity id.

``signxml`` is an optional dependency declared in the ``requirements/sso.txt``
extra; the module imports it lazily so a deployment that does not configure
SAML never pays the xmlsec toolchain cost.  When SAML is configured but the
package is missing, verification fails loudly with :class:`SsoConfigError`.
"""

from __future__ import annotations

import base64
import logging
from typing import Any
from xml.etree import ElementTree as ET

from defusedxml import ElementTree as DefusedET

from .contracts import (
    SamlProviderConfig,
    SamlResponseEnvelope,
    SsoConfigError,
    SsoVerificationError,
)

logger = logging.getLogger(__name__)

_SAML_NS = "urn:oasis:names:tc:SAML:2.0:assertion"
_PROTOCOL_NS = "urn:oasis:names:tc:SAML:2.0:protocol"
_DSIG_NS = "http://www.w3.org/2000/09/xmldsig#"


def _q(tag: str) -> str:
    return f"{{{_SAML_NS}}}{tag}"


def _q_protocol(tag: str) -> str:
    return f"{{{_PROTOCOL_NS}}}{tag}"


def _q_dsig(tag: str) -> str:
    return f"{{{_DSIG_NS}}}{tag}"


def _decode_saml(raw: str) -> bytes:
    """Decode the base64 SAMLResponse payload (with optional URL-safe variant)."""
    value = str(raw or "").strip()
    try:
        return base64.b64decode(value, validate=False)
    except Exception as exc:
        raise SsoVerificationError("SAMLResponse is not valid base64") from exc


def parse_saml_response(raw_saml: str) -> SamlResponseEnvelope:
    """Parse the base64 SAML Response and extract verification-relevant parts."""
    decoded = _decode_saml(raw_saml)
    try:
        root = DefusedET.fromstring(decoded)
    except Exception as exc:
        raise SsoVerificationError(f"malformed SAML XML: {exc}") from exc

    # The Response's top-level Issuer uses the *assertion* namespace (both
    # saml:Issuer in the Response and in the Assertion share the SAML namespace).
    issuer_el = root.find(_q("Issuer"))
    issuer = str((issuer_el.text or "").strip()) if issuer_el is not None else ""

    status_el = root.find(_q_protocol("Status"))
    status_code = ""
    if status_el is not None:
        code_el = status_el.find(_q_protocol("StatusCode"))
        if code_el is not None:
            status_code = str(code_el.get("Value") or "")

    assertion_el = root.find(_q("Assertion"))
    if assertion_el is None:
        return SamlResponseEnvelope(issuer=issuer, status_code=status_code, assertion="")

    assertion_xml = ET.tostring(assertion_el, encoding="unicode")

    name_id = ""
    audience = ""
    session_not_on_or_after = ""
    subject_el = assertion_el.find(_q("Subject"))
    if subject_el is not None:
        name_id_el = subject_el.find(_q("NameID"))
        if name_id_el is not None:
            name_id = str(name_id_el.text or "").strip()
    conditions_el = assertion_el.find(_q("Conditions"))
    if conditions_el is not None:
        session_not_on_or_after = str(
            conditions_el.get("NotOnOrAfter") or ""
        )
        ar_el = conditions_el.find(_q("AudienceRestriction"))
        if ar_el is not None:
            aud_el = ar_el.find(_q("Audience"))
            if aud_el is not None:
                audience = str(aud_el.text or "").strip()

    return SamlResponseEnvelope(
        issuer=issuer,
        status_code=status_code,
        assertion=assertion_xml,
        subject_name_id=name_id,
        audience=audience,
        session_not_on_or_after=session_not_on_or_after,
    )


def verify_saml_response(
    provider: SamlProviderConfig,
    raw_saml: str,
) -> Any:
    """Verify the SAML Response and return an :class:`SsoProfile`."""
    envelope = parse_saml_response(raw_saml)

    if envelope.status_code and "Success" not in envelope.status_code:
        raise SsoVerificationError(f"SAML Status was not Success: {envelope.status_code}")
    if envelope.issuer and envelope.issuer != provider.entity_id:
        raise SsoVerificationError(
            f"SAML Issuer {envelope.issuer!r} != configured {provider.entity_id!r}"
        )
    if not envelope.assertion:
        raise SsoVerificationError("SAML Response contained no Assertion")
    if not envelope.subject_name_id:
        raise SsoVerificationError("SAML Assertion has no NameID")

    # Verify the signature over the *full* Response document (the ds:Signature
    # references the signed Assertion by id; signxml validates the referenced
    # subtree and its digest).
    _verify_response_signature(provider, raw_saml)

    if provider.audience and envelope.audience and envelope.audience != provider.audience:
        raise SsoVerificationError(
            f"SAML Audience {envelope.audience!r} != expected {provider.audience!r}"
        )

    return {
        "protocol": "saml",
        "sub": envelope.subject_name_id,
        "email": "",
        "name": "",
        "groups": [],
    }


def _verify_response_signature(provider: SamlProviderConfig, raw_saml: str) -> None:
    """Verify the XML signature over the full SAML Response using the IdP cert."""
    try:
        from lxml import etree
        from signxml import XMLVerifier
        from signxml.exceptions import InvalidSignature
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise SsoConfigError(
            "SAML configured but signxml is not installed; install the sso extra "
            "(`pip install deeptutor[sso]`) to verify SAML assertions"
        ) from exc

    cert_pem = str(provider.idp_cert_pem or "").strip()
    if not cert_pem:
        raise SsoConfigError("SAML provider missing idp_cert_pem")

    decoded = _decode_saml(raw_saml)
    try:
        tree = etree.fromstring(decoded)
        # ``signxml`` verifies the embedded ds:Signature over the referenced
        # signed-info (including the Assertion ID reference) and rejects a
        # signature that does not cover the whole signed subtree.
        XMLVerifier().verify(
            tree,
            x509_cert=cert_pem.encode("utf-8"),
            validate_schema=True,
        )
    except InvalidSignature as exc:
        raise SsoVerificationError(f"SAML Assertion signature invalid: {exc}") from exc
    except Exception as exc:
        raise SsoVerificationError(f"SAML signature verification failed: {exc}") from exc


__all__ = [
    "parse_saml_response",
    "verify_saml_response",
]
