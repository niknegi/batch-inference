"""Unit tests for Redis token-bucket rate limiter (fakeredis)."""

from __future__ import annotations

import pytest
from fakeredis.aioredis import FakeRedis

from app.rate_limit import BatchConcurrencyGate, TokenBucketRateLimiter


@pytest.mark.asyncio
async def test_acquire_high_rate_succeeds():
    redis = FakeRedis(decode_responses=False)
    limiter = TokenBucketRateLimiter(redis)
    await limiter.acquire("test:model:key", rate=1000, capacity=1000)
    await redis.aclose()


@pytest.mark.asyncio
async def test_pause_then_acquire_times_out():
    redis = FakeRedis(decode_responses=False)
    limiter = TokenBucketRateLimiter(redis)
    await limiter.pause("test:model:key", 0.05)
    with pytest.raises(TimeoutError, match="Rate limit wait exceeded"):
        await limiter.acquire("test:model:key", rate=1000, capacity=1000, max_wait=0.01)
    await redis.aclose()


@pytest.mark.asyncio
async def test_acquire_zero_rate_is_noop():
    redis = FakeRedis(decode_responses=False)
    limiter = TokenBucketRateLimiter(redis)
    await limiter.acquire("noop", rate=0, max_wait=0.01)
    await redis.aclose()


def test_batch_concurrency_gate():
    gate = BatchConcurrencyGate()
    sem = gate.get("batch-1", 4)
    assert sem._value == 4  # noqa: SLF001
    gate.drop("batch-1")
    # recreates with new limit
    sem2 = gate.get("batch-1", 2)
    assert sem2._value == 2  # noqa: SLF001
