"""SSO provider configuration.

Read from ``<runtime-home>/data/user/settings/sso.json`` (or the admin
workspace settings dir on multi-user deployments) with environment overrides:

``DEEPTUTOR_SSO_OIDC_ISSUER``, ``DEEPTUTOR_SSO_OIDC_CLIENT_ID``,
``DEEPTUTOR_SSO_OIDC_CLIENT_SECRET``, ``DEEPTUTOR_SSO_OIDC_AUTH_URL``,
``DEEPTUTOR_SSO_OIDC_TOKEN_URL``, ``DEEPTUTOR_SSO_OIDC_JWKS_URL``,

``DEEPTUTOR_SSO_SAML_ENTITY_ID``, ``DEEPTUTOR_SSO_SAML_SSO_URL``,
``DEEPTUTOR_SSO_SAML_CERT`` (PEM), ``DEEPTUTOR_SSO_SAML_AUDIENCE``.

No secret is ever persisted in the settings file beyond what the operator
already put there; environment values are explicit deployment overrides (the
same posture as every other DeepTutor setting).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from deeptutor.services.file_io import atomic_write_json

logger = logging.getLogger(__name__)

SSO_SETTINGS_NAME = "sso.json"

DEFAULT_SSO_SETTINGS: dict[str, Any] = {
    "version": 1,
    "enabled": False,
    "oidc": {
        "issuer": "",
        "client_id": "",
        "client_secret": "",
        "authorization_endpoint": "",
        "token_endpoint": "",
        "jwks_uri": "",
        "redirect_uri": "",
        "scopes": ["openid", "profile", "email"],
    },
    "saml": {
        "entity_id": "",
        "sso_url": "",
        "idp_cert_pem": "",
        "audience": "",
        "redirect_uri": "",
    },
}

#: Environment variable overrides applied on top of the JSON file.
_OIDC_ENV_MAP = {
    "issuer": "DEEPTUTOR_SSO_OIDC_ISSUER",
    "client_id": "DEEPTUTOR_SSO_OIDC_CLIENT_ID",
    "client_secret": "DEEPTUTOR_SSO_OIDC_CLIENT_SECRET",
    "authorization_endpoint": "DEEPTUTOR_SSO_OIDC_AUTH_URL",
    "token_endpoint": "DEEPTUTOR_SSO_OIDC_TOKEN_URL",
    "jwks_uri": "DEEPTUTOR_SSO_OIDC_JWKS_URL",
    "redirect_uri": "DEEPTUTOR_SSO_OIDC_REDIRECT_URI",
}
_SAML_ENV_MAP = {
    "entity_id": "DEEPTUTOR_SSO_SAML_ENTITY_ID",
    "sso_url": "DEEPTUTOR_SSO_SAML_SSO_URL",
    "idp_cert_pem": "DEEPTUTOR_SSO_SAML_CERT",
    "audience": "DEEPTUTOR_SSO_SAML_AUDIENCE",
    "redirect_uri": "DEEPTUTOR_SSO_SAML_REDIRECT_URI",
}


def _settings_dir() -> Path:
    from deeptutor.multi_user.paths import get_admin_path_service

    return get_admin_path_service().get_settings_dir()


def sso_settings_path() -> Path:
    return _settings_dir() / SSO_SETTINGS_NAME


def load_sso_settings() -> dict[str, Any]:
    """Return the effective SSO settings (file + env overrides)."""
    payload = dict(DEFAULT_SSO_SETTINGS)
    path = sso_settings_path()
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = {**payload, **loaded}
        except Exception as exc:
            logger.warning("Unreadable SSO settings at %s: %s", path, exc)

    payload.setdefault("version", 1)
    payload.setdefault("enabled", False)
    oidc = dict(DEFAULT_SSO_SETTINGS["oidc"])
    oidc.update(payload.get("oidc") or {})
    saml = dict(DEFAULT_SSO_SETTINGS["saml"])
    saml.update(payload.get("saml") or {})
    for key, env in _OIDC_ENV_MAP.items():
        if os.getenv(env):
            oidc[key] = os.getenv(env)
    for key, env in _SAML_ENV_MAP.items():
        if os.getenv(env):
            saml[key] = os.getenv(env)
    payload["oidc"] = oidc
    payload["saml"] = saml
    payload["enabled"] = bool(payload.get("enabled"))
    return payload


def save_sso_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Persist SSO settings (environment overrides are never written back)."""
    payload = {**DEFAULT_SSO_SETTINGS, **settings}
    for section, mapping in (("oidc", _OIDC_ENV_MAP), ("saml", _SAML_ENV_MAP)):
        stored = dict(payload.get(section) or {})
        for key, env in mapping.items():
            if os.getenv(env):
                stored.pop(key, None)
        payload[section] = stored
    atomic_write_json(sso_settings_path(), payload)
    return payload


def oidc_provider_enabled() -> bool:
    return bool(load_sso_settings().get("enabled")) and bool(
        load_sso_settings()["oidc"].get("issuer")
    )


def saml_provider_enabled() -> bool:
    return bool(load_sso_settings().get("enabled")) and bool(
        load_sso_settings()["saml"].get("entity_id")
    )


__all__ = [
    "DEFAULT_SSO_SETTINGS",
    "SSO_SETTINGS_NAME",
    "load_sso_settings",
    "oidc_provider_enabled",
    "saml_provider_enabled",
    "save_sso_settings",
    "sso_settings_path",
]
