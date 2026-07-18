"""End-to-end batch flows with FakeSpaces + sqlite + MockProvider (no Docker)."""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from fakeredis.aioredis import FakeRedis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes import get_arq, get_spaces, router
from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.spaces import chunk_result_key
from app.models import Base, Batch, BatchStatus
from app.providers import MockProvider, ProviderRegistry
from app.rate_limit import BatchConcurrencyGate, TokenBucketRateLimiter
from app.services.batches import cancel_batch, create_batch, create_chunk_rows
from app.workers.jobs import finalize_batch, process_chunk
from tests.conftest import FakeSpaces


async def _worker_ctx(spaces: FakeSpaces, redis_enqueue: AsyncMock):
    fake_redis = FakeRedis()
    return {
        "redis": redis_enqueue,
        "spaces": spaces,
        "providers": ProviderRegistry({"mock": MockProvider(latency_ms=0)}),
        "limiter": TokenBucketRateLimiter(fake_redis),
        "gate": BatchConcurrencyGate(),
        "_fake_redis": fake_redis,
    }


@pytest.mark.asyncio
async def test_happy_path_create_process_finalize(db_session: AsyncSession, monkeypatch):
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
    ctx = await _worker_ctx(spaces, redis)

    chunk = chunks[0]
    result = await process_chunk(ctx, batch.id, chunk.id, chunk.chunk_index)
    assert result["ok"] is True
    assert result["ok_count"] == 5
    assert chunk_result_key(batch.id, 0) in spaces.objects

    fin = await finalize_batch(ctx, batch.id)
    assert fin["ok"] is True
    lines = spaces.objects[f"batches/{batch.id}/results.ndjson"].decode().strip().split("\n")
    assert len(lines) == 5
    assert json.loads(lines[0])["output"] == "echo:hello-0"

    await ctx["_fake_redis"].aclose()


@pytest.mark.asyncio
async def test_partial_failures_completed_with_failed_items(
    db_session: AsyncSession, monkeypatch
):
    import app.workers.jobs as jobs

    spaces = FakeSpaces()
    prompts = ["ok-one", "__fail_terminal__ no", "ok-two"]
    batch = await create_batch(
        db_session,
        spaces=spaces,  # type: ignore[arg-type]
        prompts=prompts,
        provider="mock",
        model="mock-1",
        chunk_size=10,
        rate_limit_rps=1000,
        max_concurrency=4,
    )
    chunks = await create_chunk_rows(db_session, batch)
    await db_session.commit()

    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(jobs, "get_session_factory", lambda: factory)

    redis = AsyncMock()
    redis.enqueue_job = AsyncMock()
    ctx = await _worker_ctx(spaces, redis)

    chunk = chunks[0]
    result = await process_chunk(ctx, batch.id, chunk.id, chunk.chunk_index)
    assert result["ok"] is True
    assert result["fail_count"] == 1
    assert result["ok_count"] == 2

    fin = await finalize_batch(ctx, batch.id)
    assert fin["ok"] is True

    async with factory() as s:
        b = await s.get(Batch, batch.id)
        assert b is not None
        assert b.status == BatchStatus.completed
        assert b.failed_items == 1
        assert b.completed_items == 3

    rows = [
        json.loads(ln)
        for ln in spaces.objects[f"batches/{batch.id}/results.ndjson"].decode().strip().split("\n")
    ]
    failed = [r for r in rows if not r.get("ok")]
    assert len(failed) == 1
    assert failed[0]["ok"] is False

    await ctx["_fake_redis"].aclose()


@pytest.mark.asyncio
async def test_idempotency_key_returns_same_id(db_session: AsyncSession, spaces: FakeSpaces):
    b1 = await create_batch(
        db_session,
        spaces=spaces,  # type: ignore[arg-type]
        prompts=["a", "b"],
        provider="mock",
        model="mock-1",
        idempotency_key="idem-1",
    )
    await db_session.commit()
    b2 = await create_batch(
        db_session,
        spaces=spaces,  # type: ignore[arg-type]
        prompts=["different", "prompts"],
        provider="mock",
        model="mock-1",
        idempotency_key="idem-1",
    )
    assert b1.id == b2.id
    assert b2.total_items == 2


@pytest.mark.asyncio
async def test_cancel_batch(db_session: AsyncSession, spaces: FakeSpaces):
    batch = await create_batch(
        db_session,
        spaces=spaces,  # type: ignore[arg-type]
        prompts=["x"],
        provider="mock",
        model="mock-1",
    )
    await db_session.commit()
    cancelled = await cancel_batch(db_session, batch)
    await db_session.commit()
    assert cancelled.status == BatchStatus.cancelled
    assert cancelled.completed_at is not None

    # Idempotent cancel on terminal state
    again = await cancel_batch(db_session, cancelled)
    assert again.status == BatchStatus.cancelled


@pytest.mark.asyncio
async def test_multipart_upload_endpoint():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    spaces = FakeSpaces()
    arq = AsyncMock()
    arq.enqueue_job = AsyncMock()
    settings = Settings(
        API_KEYS="test-api-key",
        MOCK_PROVIDER=True,
        DEFAULT_PROVIDER="mock",
        DEFAULT_MODEL="mock-1",
    )

    app = FastAPI()
    app.include_router(router)

    async def override_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_spaces] = lambda: spaces
    app.dependency_overrides[get_arq] = lambda: arq

    ndjson = b'{"prompt":"from-upload-1"}\n{"prompt":"from-upload-2"}\n'
    with TestClient(app) as client:
        resp = client.post(
            "/v1/batches/upload",
            headers={"Authorization": "Bearer test-api-key"},
            files={"file": ("prompts.ndjson", BytesIO(ndjson), "application/x-ndjson")},
            data={"provider": "mock", "model": "mock-1"},
        )
    assert resp.status_code == 202
    body = resp.json()
    assert body["total_items"] == 2
    assert any(k.endswith("/prompts.ndjson") for k in spaces.objects)
    arq.enqueue_job.assert_called()
    await engine.dispose()
