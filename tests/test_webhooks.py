"""Webhook HMAC signature and delivery (MockTransport)."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import patch

import httpx
import pytest

from app.services.webhooks import build_webhook_payload, deliver_webhook, sign_payload


def test_hmac_signature():
    body = b'{"event":"batch.completed"}'
    secret = "s3cret"
    sig = sign_payload(secret, body)
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert sig == expected


def test_webhook_payload_shape():
    payload = build_webhook_payload(
        event="batch.completed",
        batch_id="01ABC",
        status="completed",
        result_url="https://example.com/r",
        stats={"total_items": 10, "completed_items": 10, "failed_items": 0},
        completed_at=None,
    )
    assert payload["event"] == "batch.completed"
    assert payload["batch_id"] == "01ABC"
    assert "timestamp" in payload


async def _deliver_with_transport(
    transport: httpx.MockTransport,
    payload: dict,
    secret: str | None = None,
) -> tuple[bool, str | None]:
    real_client = httpx.AsyncClient(transport=transport, timeout=5.0)

    class _Ctx:
        async def __aenter__(self):
            return real_client

        async def __aexit__(self, *args):
            await real_client.aclose()

    with patch("app.services.webhooks.httpx.AsyncClient", return_value=_Ctx()):
        return await deliver_webhook(
            url="https://hooks.example/webhook",
            secret=secret,
            payload=payload,
        )


@pytest.mark.asyncio
async def test_deliver_webhook_success_signed():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["sig"] = request.headers.get("x-webhook-signature")
        seen["event"] = request.headers.get("x-webhook-event")
        seen["batch_id"] = request.headers.get("x-batch-id")
        seen["body"] = request.content
        return httpx.Response(204)

    ok, err = await _deliver_with_transport(
        httpx.MockTransport(handler),
        {"event": "batch.completed", "batch_id": "b1"},
        secret="topsecret",
    )
    assert ok is True
    assert err is None
    assert seen["sig"] == sign_payload("topsecret", seen["body"])
    assert seen["event"] == "batch.completed"
    assert seen["batch_id"] == "b1"
    # body matches canonical JSON used by deliver_webhook
    assert json.loads(seen["body"])["batch_id"] == "b1"


@pytest.mark.asyncio
async def test_deliver_webhook_http_4xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request")

    ok, err = await _deliver_with_transport(
        httpx.MockTransport(handler),
        {"event": "batch.completed", "batch_id": "b1"},
    )
    assert ok is False
    assert err is not None
    assert "HTTP 400" in err


@pytest.mark.asyncio
async def test_deliver_webhook_transport_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    ok, err = await _deliver_with_transport(
        httpx.MockTransport(handler),
        {"event": "batch.completed", "batch_id": "b1"},
    )
    assert ok is False
    assert err is not None
