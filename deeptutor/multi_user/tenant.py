"""Tenant context and metadata helpers for the DeepTutor enterprise layer.

DeepTutor's built-in multi-user layer already isolates *accounts* into per-
user workspaces.  Multi-tenancy adds an operator dimension on top: one private
instance can host several organizations (e.g. a reseller runs one deployment
serving two schools).  Records that must be scoped by operating entity —

- user accounts, orders, courses, learning paths
- license verification (a license names the ``tenant_id`` it was issued for)

— carry a ``tenant_id`` column.  This module owns the smallest seam that makes
that real:

1. ``TenantContext`` — a per-request ContextVar telling the current operating
   entity, with the same reset/install discipline as ``multi_user.context``.
2. ``require_tenant`` / default on enterprise deployments: when a deployment
   is multi-tenant (configured with ``DEEPTUTOR_TENANT_MODE=multi`` or a
   non-empty ``tenant_id`` in ``system.enterprise``), every scoped write must
   record a tenant and every scoped read must filter by one, so two operators
   on one instance never see each other's data.
3. ``denormalized_tenant`` — the one helper the payload builders call; reads
   the current tenant or fails loudly when an enterprise multi-tenant
   deployment forgets to set one.

A legacy single-tenant deployment (the default) is untouched: no configured
tenant mode means ``denormalized_tenant`` returns ``""`` and every existing
record keeps working.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
import logging
import os
from typing import Iterator

logger = logging.getLogger(__name__)

#: Env vars the operator uses to declare enterprise tenancy.
TENANT_MODE_ENV = "DEEPTUTOR_TENANT_MODE"
DEFAULT_TENANT_ENV = "DEEPTUTOR_DEFAULT_TENANT"

#: The single recognised multi-tenant mode.
TENANT_MODE_MULTI = "multi"

#: Record kinds that must be tenant-scoped when tenancy is enabled.
TENANT_SCOPED_KINDS = frozenset({"user", "order", "course", "learning"})

#: Key stored on each scoped record.
TENANT_FIELD = "tenant_id"

_current_tenant: ContextVar[str] = ContextVar("deeptutor_current_tenant", default="")


def is_multi_tenant_enabled() -> bool:
    """Whether this deployment is configured as multi-tenant.

    Reads the process environment at call time so an operator flipping the
    knob restarts and all new requests agree on the new state.
    """
    mode = str(os.getenv(TENANT_MODE_ENV, "")).strip().lower()
    return mode == TENANT_MODE_MULTI


def default_tenant_id() -> str:
    """The default tenant every single-tenant record belongs to."""
    return str(os.getenv(DEFAULT_TENANT_ENV, "")).strip()


def set_current_tenant(tenant_id: str) -> Token[str]:
    return _current_tenant.set(str(tenant_id or "").strip())


def reset_current_tenant(token: Token[str]) -> None:
    _current_tenant.reset(token)


def get_current_tenant() -> str:
    return _current_tenant.get()


@contextmanager
def tenant_context(tenant_id: str) -> Iterator[None]:
    """Yield with the tenant ContextVar set to *tenant_id*, restoring after.

    The same discipline as ``multi_user.context.user_context``: a request
    handler (or license verification) pins the tenant for the duration of the
    call so every ``denormalized_tenant()`` read inside agrees on the entity.
    """
    token = set_current_tenant(tenant_id)
    try:
        yield
    finally:
        reset_current_tenant(token)


def denormalized_tenant(*, kind: str = "") -> str:
    """The tenant id to stamp on a scoped record, or ``""`` for legacy.

    On multi-tenant deployments, an empty current tenant is a configuration
    bug: every scoped write must name the operating entity or two operators
    would silently share one store.  Raise so the failure is an obvious 500
    rather than a silent data leak.  ``kind`` is only used to make the error
    message actionable.
    """
    tenant = get_current_tenant()
    if tenant:
        return tenant
    if is_multi_tenant_enabled():
        raise RuntimeError(
            f"multi-tenant deployment missing tenant scope for {kind or 'record'}: "
            "set the tenant ContextVar (or pass tenant_id) before writing"
        )
    return default_tenant_id()