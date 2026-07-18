"""Unit tests for chunk planning, webhooks, rate limiter, routing, and backoff."""

from __future__ import annotations

import hashlib
import hmac

import pytest
from fakeredis.aioredis import FakeRedis

from app.core.backoff import exponential_backoff_seconds, webhook_backoff_seconds
from app.core.config import Settings
from app.providers import MockProvider
from app.providers.base import InferenceRequest
from app.rate_limit import TokenBucketRateLimiter
from app.services.batches import plan_chunks
from app.services.routing import resolve_model
from app.services.webhooks import build_webhook_payload, sign_payload
from app.services.webhooks import webhook_backoff_seconds as webhook_backoff_reexport


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
    assert webhook_backoff_seconds(1, jitter=False) == 1
    assert webhook_backoff_seconds(2, jitter=False) == 2
    assert webhook_backoff_seconds(10, jitter=False) == 300
    assert webhook_backoff_reexport is webhook_backoff_seconds


def test_webhook_backoff_jitter_in_range():
    for attempt in (1, 2, 5, 10):
        cap = min(2 ** max(attempt - 1, 0), 300)
        for _ in range(20):
            v = webhook_backoff_seconds(attempt, jitter=True)
            assert 0.0 <= v <= float(cap)


def test_exponential_backoff_no_jitter():
    assert exponential_backoff_seconds(0, jitter=False) == 1.0
    assert exponential_backoff_seconds(1, jitter=False) == 2.0
    assert exponential_backoff_seconds(3, jitter=False) == 8.0
    assert exponential_backoff_seconds(10, jitter=False) == 8.0


def test_exponential_backoff_jitter_in_range():
    for attempt in (0, 1, 2, 5):
        cap = min(8.0, 1.0 * (2**attempt))
        for _ in range(20):
            v = exponential_backoff_seconds(attempt, jitter=True)
            assert 0.0 <= v <= cap


def test_resolve_model_explicit_override():
    settings = Settings(
        DEFAULT_PROVIDER="mock",
        DEFAULT_MODEL="llama3.2-3b-instruct",
        DEFAULT_COST_PREFERENCE="economy",
    )
    choice = resolve_model(
        provider="digitalocean",
        model="llama3.3-70b-instruct",
        cost_preference="economy",
        settings=settings,
    )
    assert choice.provider == "digitalocean"
    assert choice.model == "llama3.3-70b-instruct"
    assert choice.reason == "explicit_model_override"


def test_resolve_model_economy_picks_small_llama():
    settings = Settings(
        DEFAULT_PROVIDER="digitalocean",
        DEFAULT_MODEL="llama3.3-70b-instruct",
        DEFAULT_COST_PREFERENCE="economy",
    )
    # Explicit default model is too expensive for economy → pick cheapest in tier
    choice = resolve_model(
        provider="digitalocean",
        model=None,
        cost_preference="economy",
        settings=settings,
    )
    assert choice.provider == "digitalocean"
    assert choice.model == "llama3.2-3b-instruct"
    assert choice.cost_tier == "economy"
    assert choice.reason == "cost_preference_economy"


def test_resolve_model_settings_default():
    settings = Settings(
        DEFAULT_PROVIDER="mock",
        DEFAULT_MODEL="mock-1",
        DEFAULT_COST_PREFERENCE="economy",
    )
    choice = resolve_model(
        provider=None,
        model=None,
        cost_preference=None,
        settings=settings,
    )
    assert choice.provider == "mock"
    assert choice.model == "mock-1"
    assert choice.reason == "settings_default_model"


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
