"""Webhook event delivery for the DeepTutor Enterprise layer.

D4 open-integration events (course published, order fulfilled, learning report
generated, appointment booked) are pushed to operator-configured HTTP
endpoints with HMAC-SHA256 signatures so a receiver can verify authenticity.

Design (KISS — no workflow engine, no outbox broker):

- Endpoints are registered in ``<runtime-home>/data/system/webhooks.json`` as
  ``{url, secret, enabled, events: [...]}``.
- ``emit_event`` delivers best-effort in-process, synchronously, with a small
  retry budget and an exponential backoff capped at 2s.  Events that cannot be
  delivered are appended to ``data/system/webhooks/undelivered.jsonl`` (one
  JSON object per line) so an operator can replay them — a durable audit trail
  without adding a queue dependency.
- Signature: ``X-DeepTutor-Signature: sha256=<hex>` where the hex is
  ``HMAC-SHA256(secret, canonical_body)`` and ``canonical_body`` is the raw
  UTF-8 bytes of the JSON body sent.  Receivers recompute and compare
  constant-time.
- A timestamp header ``X-DeepTutor-Event-Id`` and ``X-DeepTutor-Timestamp``
  lets receivers reject replay/tamper windows.

Events never carry secrets: the payload is the public event shape only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from pathlib import Path
import secrets
import threading
import time
from typing import Any

import httpx

from deeptutor.multi_user import paths as mu_paths
from deeptutor.services.file_io import atomic_write_json

logger = logging.getLogger(__name__)

WEBHOOKS_DIRNAME = "webhooks"
WEBHOOKS_FILE_NAME = "webhooks.json"
UNDELIVERED_FILE_NAME = "undelivered.jsonl"

#: Canonical event names the API layer can emit.
EVENT_COURSE_PUBLISHED = "course.published"
EVENT_ORDER_FULFILLED = "order.fulfilled"
EVENT_LEARNING_REPORT = "learning.report"
EVENT_APPOINTMENT_BOOKED = "appointment.booked"

_EVENT_NAMES = frozenset(
    {
        EVENT_COURSE_PUBLISHED,
        EVENT_ORDER_FULFILLED,
        EVENT_LEARNING_REPORT,
        EVENT_APPOINTMENT_BOOKED,
    }
)

_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 0.2
_MAX_BACKOFF = 2.0

_WRITE_LOCK = threading.RLock()

#: Max bytes of the undelivered journal before it rotates (avoid unbounded growth).
_MAX_UNDELIVERED_BYTES = 10 * 1024 * 1024


def webhooks_root() -> Path:
    mu_paths.ensure_system_dirs()
    root = mu_paths.SYSTEM_ROOT / WEBHOOKS_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def webhooks_path() -> Path:
    return webhooks_root() / WEBHOOKS_FILE_NAME


def undelivered_path() -> Path:
    return webhooks_root() / UNDELIVERED_FILE_NAME


def _empty_store() -> dict[str, Any]:
    return {"version": 1, "webhooks": []}


def load_webhooks() -> list[dict[str, Any]]:
    path = webhooks_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("Unreadable webhook store at %s: %s", path, exc)
        return []
    raw = payload.get("webhooks") if isinstance(payload, dict) else None
    return [h for h in raw if isinstance(h, dict)] if isinstance(raw, list) else []


def save_webhooks(webhooks: list[dict[str, Any]]) -> None:
    with _WRITE_LOCK:
        atomic_write_json(webhooks_path(), {"version": 1, "webhooks": webhooks})


def register_webhook(
    *,
    url: str,
    secret: str = "",
    events: list[str] | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    """Register a webhook endpoint (idempotent per URL)."""
    url = str(url or "").strip()
    if not url:
        raise ValueError("webhook url is required")
    events = [e for e in (events or []) if e in _EVENT_NAMES]
    if not events:
        events = sorted(_EVENT_NAMES)
    record = {
        "id": f"wh_{secrets.token_hex(6)}",
        "url": url,
        "secret": secret or secrets.token_urlsafe(24),
        "events": events,
        "enabled": enabled,
        "created_at": time.time(),
    }
    with _WRITE_LOCK:
        webhooks = load_webhooks()
        for existing in webhooks:
            if existing.get("url") == url:
                # Preserve the original id (stable handle for unregister/replay).
                record["id"] = existing.get("id") or record["id"]
                record["created_at"] = existing.get("created_at") or record["created_at"]
                existing.update(record)
                save_webhooks(webhooks)
                return existing
        webhooks.append(record)
        save_webhooks(webhooks)
    return record


def unregister_webhook(webhook_id: str) -> bool:
    with _WRITE_LOCK:
        webhooks = load_webhooks()
        before = len(webhooks)
        webhooks = [h for h in webhooks if h.get("id") != webhook_id]
        save_webhooks(webhooks)
        return len(webhooks) < before


def emit_event(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Emit *event* to every enabled webhook subscribed to it.

    Returns a per-webhook outcome map.  Delivery failures are journaled to the
    undelivered log so nothing is silently lost.
    """
    if event not in _EVENT_NAMES:
        raise ValueError(f"unknown event {event!r}")
    event_id = f"evt_{secrets.token_hex(10)}"
    timestamp = int(time.time())
    body = json.dumps(
        {"event": event, "event_id": event_id, "timestamp": timestamp, "data": payload},
        ensure_ascii=False,
    ).encode("utf-8")

    outcomes: dict[str, Any] = {}
    webhooks = load_webhooks()
    for hook in webhooks:
        hook_id = str(hook.get("id") or "")
        if not hook.get("enabled", True):
            outcomes[hook_id] = {"ok": False, "reason": "disabled"}
            continue
        if event not in (hook.get("events") or []):
            outcomes[hook_id] = {"ok": False, "reason": "not-subscribed"}
            continue
        ok = _deliver(
            url=str(hook.get("url") or ""),
            secret=str(hook.get("secret") or ""),
            event_id=event_id,
            timestamp=timestamp,
            body=body,
        )
        outcomes[hook_id] = {"ok": ok, "url": hook.get("url")}
        if not ok:
            _journal_undelivered(
                {
                    "event": event,
                    "event_id": event_id,
                    "timestamp": timestamp,
                    "url": hook.get("url"),
                    "payload": payload,
                }
            )
    return {"event_id": event_id, "outcomes": outcomes}


def _deliver(
    *,
    url: str,
    secret: str,
    event_id: str,
    timestamp: int,
    body: bytes,
) -> bool:
    signature = sign_payload(secret, body)
    headers = {
        "Content-Type": "application/json",
        "X-DeepTutor-Signature": f"sha256={signature}",
        "X-DeepTutor-Event-Id": event_id,
        "X-DeepTutor-Timestamp": str(timestamp),
        "User-Agent": "DeepTutor-Webhook/1",
    }
    delay = _RETRY_BACKOFF_SECONDS
    last_error: Exception | None = None
    for attempt in range(_RETRIES):
        try:
            resp = httpx.post(url, content=body, headers=headers, timeout=10)
            if 200 <= resp.status_code < 300:
                return True
            last_error = RuntimeError(f"HTTP {resp.status_code}")
        except Exception as exc:  # network / DNS / timeout
            last_error = exc
        time.sleep(delay)
        delay = min(delay * 2, _MAX_BACKOFF)
    logger.warning("webhook delivery failed url=%s err=%s", url, last_error)
    return False


def sign_payload(secret: str, body: bytes) -> str:
    """HMAC-SHA256 hex signature over the raw request body."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, signature: str) -> bool:
    """Constant-time comparison of an incoming signature."""
    expected = sign_payload(secret, body)
    return hmac.compare_digest(expected, str(signature or ""))


def _journal_undelivered(record: dict[str, Any]) -> None:
    path = undelivered_path()
    try:
        with _WRITE_LOCK:
            line = json.dumps(record, ensure_ascii=False) + "\n"
            if path.exists() and path.stat().st_size > _MAX_UNDELIVERED_BYTES:
                path.rename(path.with_suffix(".old.jsonl"))
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
    except OSError as exc:
        logger.error("could not journal undelivered webhook event: %s", exc)


def replay_undelivered() -> int:
    """Re-attempt every journaled undelivered event (best-effort)."""
    path = undelivered_path()
    if not path.exists():
        return 0
    sent = 0
    with _WRITE_LOCK:
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if _deliver_now(record):
                sent += 1
        path.unlink(missing_ok=True)
    return sent


def _deliver_now(record: dict[str, Any]) -> bool:
    """A single delivery attempt for a journaled record (no retry loop)."""
    try:
        url = str(record.get("url") or "")
        resp = httpx.post(url, json=record, timeout=10)
        return 200 <= resp.status_code < 300
    except Exception:
        return False


__all__ = [
    "EVENT_APPOINTMENT_BOOKED",
    "EVENT_COURSE_PUBLISHED",
    "EVENT_LEARNING_REPORT",
    "EVENT_ORDER_FULFILLED",
    "emit_event",
    "load_webhooks",
    "register_webhook",
    "replay_undelivered",
    "save_webhooks",
    "sign_payload",
    "unregister_webhook",
    "verify_signature",
]
