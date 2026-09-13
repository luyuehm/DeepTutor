"""Webhook delivery tests.

The critical properties:
- endpoints can be registered/unregistered and events filtered by type
- payloads are HMAC-SHA256 signed (verifiable by a receiver)
- delivery is retried and undelivered events are journaled
- signature verification is constant-time and rejects tampering
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import httpx
import pytest

from deeptutor.multi_user import paths as mu_paths
from deeptutor.services.webhooks import service as webhooks_module
from deeptutor.services.webhooks.service import (
    EVENT_COURSE_PUBLISHED,
    EVENT_ORDER_FULFILLED,
    emit_event,
    register_webhook,
    sign_payload,
    unregister_webhook,
    verify_signature,
)


@pytest.fixture()
def isolated_webhook_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setattr(mu_paths, "ADMIN_WORKSPACE_ROOT", data_root.resolve())
    monkeypatch.setattr(mu_paths, "SYSTEM_ROOT", data_root / "system")
    monkeypatch.setattr(mu_paths, "_path_services", {})
    webhooks_module.mu_paths = mu_paths
    yield data_root
    monkeypatch.undo()


def test_register_and_load(isolated_webhook_root: Path) -> None:
    record = register_webhook(
        url="https://hooks.example.test/dt",
        secret="s3cret",
        events=[EVENT_ORDER_FULFILLED],
    )
    assert record["secret"] == "s3cret"
    loaded = next(
        h for h in webhooks_module.load_webhooks() if h["id"] == record["id"]
    )
    assert loaded["events"] == [EVENT_ORDER_FULFILLED]


def test_register_is_idempotent_per_url(isolated_webhook_root: Path) -> None:
    a = register_webhook(url="https://hooks.example.test/x", secret="s1")
    b = register_webhook(url="https://hooks.example.test/x", secret="s2")
    assert a["id"] == b["id"]
    assert b["secret"] == "s2"  # the second registration updated it
    assert len(webhooks_module.load_webhooks()) == 1


def test_signature_verify_roundtrip() -> None:
    body = json.dumps({"event": "order.fulfilled"}).encode()
    sig = sign_payload("secret", body)
    assert verify_signature("secret", body, sig)
    assert not verify_signature("secret", body, sig[:-1] + ("0" if sig[-1] != "0" else "1"))


def test_emit_delivers_to_subscribed_endpoint(
    isolated_webhook_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import deeptutor.services.webhooks.service as wh

    calls: list[tuple[str, dict, dict]] = []

    def _fake_post(url, *, content, headers, timeout):
        calls.append((str(url), content, headers))
        return httpx.Response(200, text="ok")

    monkeypatch.setattr(wh.httpx, "post", _fake_post)

    record = register_webhook(
        url="https://hooks.example.test/dt",
        secret="s3cret",
        events=[EVENT_ORDER_FULFILLED],
    )
    outcome = emit_event(EVENT_ORDER_FULFILLED, {"order_id": "po_1"})
    assert outcome["outcomes"][record["id"]]["ok"] is True
    url, content, headers = calls[0]
    assert url == "https://hooks.example.test/dt"
    body = json.loads(content.decode())
    assert body["event"] == EVENT_ORDER_FULFILLED
    assert body["data"]["order_id"] == "po_1"
    # Signature header is present and verifiable against the raw body.
    sig = headers["X-DeepTutor-Signature"]  # sha256=<hex>
    assert sig.startswith("sha256=")
    assert verify_signature("s3cret", content, sig[7:])


def test_emit_skips_unsubscribed_event(isolated_webhook_root: Path) -> None:
    record = register_webhook(
        url="https://hooks.example.test/dt",
        secret="s3cret",
        events=[EVENT_ORDER_FULFILLED],
    )
    outcome = emit_event(EVENT_COURSE_PUBLISHED, {"course_id": "c1"})
    assert outcome["outcomes"][record["id"]]["ok"] is False
    assert outcome["outcomes"][record["id"]]["reason"] == "not-subscribed"


def test_undelivered_events_are_journaled(
    isolated_webhook_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import deeptutor.services.webhooks.service as wh

    monkeypatch.setattr(wh, "_deliver", lambda **kw: False)
    record = register_webhook(
        url="https://hooks.example.test/dt",
        secret="s3cret",
        events=[EVENT_ORDER_FULFILLED],
    )
    outcome = wh.emit_event(EVENT_ORDER_FULFILLED, {"order_id": "po_1"})
    assert outcome["outcomes"][record["id"]]["ok"] is False
    journal = wh.undelivered_path()
    assert journal.exists()
    lines = journal.read_text(encoding="utf-8").splitlines()
    assert any("po_1" in line for line in lines)


def test_unregister_removes_endpoint(isolated_webhook_root: Path) -> None:
    record = register_webhook(url="https://hooks.example.test/x")
    assert unregister_webhook(record["id"])
    assert webhooks_module.load_webhooks() == []