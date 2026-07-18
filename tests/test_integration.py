"""Integration-style tests with in-memory / mock components (no Docker required)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fakeredis.aioredis import FakeRedis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.spaces import chunk_result_key, prompts_key
from app.models import Batch
from app.providers import MockProvider, ProviderRegistry
from app.rate_limit import BatchConcurrencyGate, TokenBucketRateLimiter
from app.services.batches import create_batch, create_chunk_rows, plan_chunks
from app.workers.jobs import finalize_batch, process_chunk
from tests.conftest import FakeSpaces


@pytest.mark.asyncio
async def test_create_batch_and_chunks(db_session: AsyncSession, spaces: FakeSpaces):
    prompts = [f"p{i}" for i in range(250)]
    batch = await create_batch(
        db_session,
        spaces=spaces,  # type: ignore[arg-type]
        prompts=prompts,
        provider="mock",
        model="mock-1",
        chunk_size=100,
        rate_limit_rps=1000,
        max_concurrency=8,
    )
    await db_session.commit()
    assert batch.total_items == 250
    assert batch.prompts_key == prompts_key(batch.id)
    assert batch.prompts_key in spaces.objects

    chunks = await create_chunk_rows(db_session, batch)
    await db_session.commit()
    assert len(chunks) == 3
    assert plan_chunks(250, 100)[2][2] == 50


@pytest.mark.asyncio
async def test_process_and_finalize_chunk_flow(db_session: AsyncSession, monkeypatch):
    import app.workers.jobs as jobs

    spaces = FakeSpaces()
    prompts = [f"hello-{i}" for i in range(5)]
    batch = await create_batch(
        db_session,
        spaces=spaces,  # type: ignore[arg-type]
        prompts=prompts,
        provider="mock",
        model="mock-1",
        chunk_size=5,
        rate_limit_rps=1000,
        max_concurrency=4,
    )
    chunks = await create_chunk_rows(db_session, batch)
    await db_session.commit()

    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(jobs, "get_session_factory", lambda: factory)

    redis = AsyncMock()
    redis.enqueue_job = AsyncMock()

    fake_redis = FakeRedis()
    ctx = {
        "redis": redis,
        "spaces": spaces,
        "providers": ProviderRegistry({"mock": MockProvider(latency_ms=0)}),
        "limiter": TokenBucketRateLimiter(fake_redis),
        "gate": BatchConcurrencyGate(),
    }

    chunk = chunks[0]
    result = await process_chunk(ctx, batch.id, chunk.id, chunk.chunk_index)
    assert result["ok"] is True
    assert result["ok_count"] == 5
    assert chunk_result_key(batch.id, 0) in spaces.objects

    async with factory() as s:
        b = await s.get(Batch, batch.id)
        assert b is not None
        assert b.completed_items == 5

    fin = await finalize_batch(ctx, batch.id)
    assert fin["ok"] is True
    assert f"batches/{batch.id}/results.ndjson" in spaces.objects
    lines = spaces.objects[f"batches/{batch.id}/results.ndjson"].decode().strip().split("\n")
    assert len(lines) == 5
    assert json.loads(lines[0])["output"] == "echo:hello-0"
    redis.enqueue_job.assert_called()
    await fake_redis.aclose()
