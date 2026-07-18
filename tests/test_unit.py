"""Unit tests for chunk planning, webhooks, rate limiter, and providers."""

from __future__ import annotations

import hashlib
import hmac

import pytest
from fakeredis.aioredis import FakeRedis

from app.providers import MockProvider
from app.providers.base import InferenceRequest
from app.rate_limit import TokenBucketRateLimiter
from app.services.batches import plan_chunks
from app.services.webhooks import build_webhook_payload, sign_payload, webhook_backoff_seconds


def test_plan_chunks_basic():
    assert plan_chunks(250, 100) == [(0, 0, 100), (1, 100, 100), (2, 200, 50)]


def test_plan_chunks_exact():
    assert plan_chunks(200, 100) == [(0, 0, 100), (1, 100, 100)]


def test_plan_chunks_scale_500k():
    chunks = plan_chunks(500_000, 100)
    assert len(chunks) == 5_000
    assert chunks[0] == (0, 0, 100)
    assert chunks[-1] == (4999, 499_900, 100)
    assert sum(c[2] for c in chunks) == 500_000


def test_webhook_signature():
    body = b'{"event":"batch.completed"}'
    secret = "s3cret"
    sig = sign_payload(secret, body)
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert sig == expected


def test_webhook_backoff_caps():
    assert webhook_backoff_seconds(1) == 1
    assert webhook_backoff_seconds(2) == 2
    assert webhook_backoff_seconds(10) == 300


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


@pytest.mark.asyncio
async def test_mock_provider():
    p = MockProvider(latency_ms=0)
    result = await p.infer(
        InferenceRequest(prompt="hello", model="mock-1", provider="mock", index=0)
    )
    assert result.ok
    assert result.output == "echo:hello"
    await p.aclose()


@pytest.mark.asyncio
async def test_token_bucket_rate_limiter():
    redis = FakeRedis(decode_responses=False)
    limiter = TokenBucketRateLimiter(redis)
    # High rate should not block
    await limiter.acquire("test:model:key", rate=1000, capacity=1000)
    await limiter.pause("test:model:key", 0.05)
    with pytest.raises(TimeoutError):
        await limiter.acquire("test:model:key", rate=1000, capacity=1000, max_wait=0.01)
    await redis.aclose()
