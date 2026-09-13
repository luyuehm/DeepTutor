"""SSO endpoints: OIDC authorization-code + SAML HTTP-POST.

The SSO surface mounts under ``/api/auth/sso`` and is intentionally *public*
(unlike the admin-gated license API) — the IdP callback cannot carry a
session JWT, and the authorization-redirect must be reachable before login.
Every endpoint therefore declares its own guards.

Flow (both protocols):

1. ``GET /login/oidc`` / ``GET /login/saml`` — build the IdP authorization
   URL / render a POST form, storing a fresh ``state`` in the session cookie.
2. The IdP redirects back to ``/callback/oidc`` / ``/callback/saml`` with a
   code / SAMLResponse.
3. The response is verified (JWKS signature / SAML cert + audience), mapped to
   a local account, and the normal DeepTutor session cookie is issued.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from deeptutor.services.sso import service as sso_service
from deeptutor.services.sso.config import load_sso_settings

logger = logging.getLogger(__name__)

router = APIRouter()

_OIDC_STATE_COOKIE = "dt_sso_oidc_state"
_SAML_STATE_COOKIE = "dt_sso_saml_state"
_STATE_MAX_AGE = 600


class SsoSessionIssue(BaseModel):
    """Response payload after a successful SSO login."""

    ok: bool = True
    username: str = ""
    role: str = ""
    is_admin: bool = False


def _oidc_config() -> dict[str, Any]:
    settings = load_sso_settings()
    if not settings.get("enabled"):
        raise HTTPException(status_code=400, detail="SSO is not enabled")
    oidc = settings.get("oidc") or {}
    if not oidc.get("issuer") or not oidc.get("client_id") or not oidc.get("client_secret"):
        raise HTTPException(status_code=400, detail="OIDC provider is not configured")
    return oidc


def _saml_config() -> dict[str, Any]:
    settings = load_sso_settings()
    if not settings.get("enabled"):
        raise HTTPException(status_code=400, detail="SSO is not enabled")
    saml = settings.get("saml") or {}
    if not saml.get("entity_id") or not saml.get("sso_url") or not saml.get("idp_cert_pem"):
        raise HTTPException(status_code=400, detail="SAML provider is not configured")
    return saml


def _issue_session(account: dict[str, Any], tenant_id: str = "") -> tuple[str, SsoSessionIssue]:
    """Mint a session token and a response body for an SSO-mapped account."""
    from deeptutor.services.auth import create_token as _create_token

    username = str(account.get("username") or "")
    role = str(account.get("role") or "user")
    token = _create_token(
        username, role=role, user_id=str(account.get("id") or ""), tenant_id=tenant_id
    )
    return token, SsoSessionIssue(username=username, role=role, is_admin=role == "admin")


@router.get("/login/oidc")
async def sso_oidc_login(request: Request) -> RedirectResponse:
    """Redirect the browser to the OIDC IdP authorization endpoint."""
    from deeptutor.services.sso.contracts import OidcProviderConfig

    oidc = _oidc_config()
    state = sso_service.new_state()
    provider = OidcProviderConfig(
        issuer=str(oidc["issuer"]),
        client_id=str(oidc["client_id"]),
        client_secret=str(oidc["client_secret"]),
        authorization_endpoint=str(oidc.get("authorization_endpoint") or ""),
        token_endpoint=str(oidc.get("token_endpoint") or ""),
        jwks_uri=str(oidc.get("jwks_uri") or ""),
        redirect_uri=str(oidc.get("redirect_uri") or f"{request.base_url}api/auth/sso/callback/oidc"),
        scopes=[*oidc.get("scopes")] if oidc.get("scopes") else ["openid", "profile", "email"],
    )
    url = sso_service.build_oidc_authorization_url(provider, state, nonce=sso_service.new_nonce())
    resp = RedirectResponse(url)
    resp.set_cookie(_OIDC_STATE_COOKIE, state, httponly=True, max_age=_STATE_MAX_AGE, samesite="lax")
    return resp


@router.get("/callback/oidc")
async def sso_oidc_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    dt_sso_oidc_state: str | None = Cookie(default=None),
) -> Any:
    """Exchange the code, verify, and create a session."""
    from deeptutor.services.sso.contracts import OidcProviderConfig

    oidc = _oidc_config()
    expected = dt_sso_oidc_state or ""
    if not code or not state:
        raise HTTPException(status_code=400, detail="missing code or state")
    provider = OidcProviderConfig(
        issuer=str(oidc["issuer"]),
        client_id=str(oidc["client_id"]),
        client_secret=str(oidc["client_secret"]),
        authorization_endpoint=str(oidc.get("authorization_endpoint") or ""),
        token_endpoint=str(oidc.get("token_endpoint") or ""),
        jwks_uri=str(oidc.get("jwks_uri") or ""),
        redirect_uri=str(oidc.get("redirect_uri") or f"{request.base_url}api/auth/sso/callback/oidc"),
        scopes=[*oidc.get("scopes")] if oidc.get("scopes") else ["openid", "profile", "email"],
    )
    try:
        profile = await sso_service.exchange_oidc_code(
            provider,
            code=code,
            redirect_uri=provider.redirect_uri,
            state=state,
            expected_state=expected,
        )
    except sso_service.SsoError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    account = sso_service.upsert_sso_user(profile)
    token, body = _issue_session(account)
    resp = Response(content=body.model_dump_json(), media_type="application/json")
    resp.set_cookie("dt_token", token, httponly=True, max_age=24 * 3600, samesite="lax")
    return resp


@router.get("/login/saml")
async def sso_saml_login(request: Request) -> HTMLResponse:
    """Render an auto-submitting SAML HTTP-POST form to the IdP."""
    saml = _saml_config()
    relay_state = sso_service.new_state()
    form = (
        "<!doctype html><html><body>"
        "<form method='POST' action='{sso_url}'>"
        "<input type='hidden' name='RelayState' value='{relay}'>"
        "<noscript><button type='submit'>Continue to SSO</button></noscript>"
        "</form><script>document.forms[0].submit()</script></body></html>"
    ).format(sso_url=str(saml["sso_url"]), relay=relay_state)
    resp = HTMLResponse(form)
    resp.set_cookie(_SAML_STATE_COOKIE, relay_state, httponly=True, max_age=_STATE_MAX_AGE, samesite="lax")
    return resp


@router.post("/callback/saml")
async def sso_saml_callback(
    request: Request, response: Response
) -> dict[str, Any]:
    from deeptutor.services.sso.contracts import SamlProviderConfig

    saml = _saml_config()
    body = await request.form()
    raw_saml = str(body.get("SAMLResponse") or "")
    relay_state = str(body.get("RelayState") or "")
    expected = request.cookies.get(_SAML_STATE_COOKIE) or ""

    if not raw_saml:
        raise HTTPException(status_code=400, detail="missing SAMLResponse")
    provider = SamlProviderConfig(
        entity_id=str(saml["entity_id"]),
        sso_url=str(saml["sso_url"]),
        idp_cert_pem=_unquote_cert(str(saml.get("idp_cert_pem") or "")),
        audience=str(saml.get("audience") or ""),
        redirect_uri=str(saml.get("redirect_uri") or""),
    )
    try:
        profile = await sso_service.verify_saml_response(
            provider, raw_saml, expected_relay_state=expected, relay_state=relay_state
        )
    except sso_service.SsoError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    account = sso_service.upsert_sso_user(profile)
    token, body_payload = _issue_session(account)
    response.set_cookie("dt_token", token, httponly=True, max_age=24 * 3600, samesite="lax")
    return body_payload.model_dump()


def _unquote_cert(value: str) -> str:
    """Certificates in settings may be stored with escaped newlines."""
    return value.replace("\\n", "\n").strip()


@router.get("/status")
async def sso_status() -> dict[str, Any]:
    settings = load_sso_settings()
    return {
        "enabled": bool(settings.get("enabled")),
        "oidc": bool(
            settings.get("enabled")
            and settings.get("oidc", {}).get("issuer")
        ),
        "saml": bool(
            settings.get("enabled")
            and settings.get("saml", {}).get("entity_id")
        ),
    }