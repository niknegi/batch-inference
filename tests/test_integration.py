"""Integration-style tests with in-memory / mock components (no Docker required)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.spaces import chunk_result_key, prompts_key
from app.models import Base, Batch
from app.providers import MockProvider, ProviderRegistry
from app.rate_limit import BatchConcurrencyGate, TokenBucketRateLimiter
from app.services.batches import create_batch, create_chunk_rows, plan_chunks
from app.workers.jobs import finalize_batch, process_chunk


class FakeSpaces:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def upload_prompts_ndjson(self, key: str, prompts) -> int:
        lines = []
        for i, p in enumerate(prompts):
            if isinstance(p, str):
                obj = {"index": i, "prompt": p}
            else:
                obj = {"index": i, **p}
            lines.append(json.dumps(obj).encode())
        self.objects[key] = b"\n".join(lines) + b"\n"
        return len(prompts)

    async def read_line_range(self, key: str, offset: int, limit: int) -> list[dict]:
        data = self.objects[key].decode().strip().split("\n")
        rows = [json.loads(line) for line in data if line]
        return rows[offset : offset + limit]

    async def write_chunk_results(self, key: str, rows) -> str:
        body = b"\n".join(json.dumps(r).encode() for r in rows) + b"\n"
        self.objects[key] = body
        return key

    async def concatenate_chunks(self, chunk_keys, dest_key: str) -> str:
        parts = [self.objects[k] for k in chunk_keys]
        self.objects[dest_key] = b"".join(parts)
        return dest_key

    async def put_json(self, key: str, payload: Any) -> str:
        self.objects[key] = json.dumps(payload).encode()
        return key

    async def generate_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return f"https://spaces.example/{key}?exp={expires_in}"


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_batch_and_chunks(db_session: AsyncSession, monkeypatch):
    spaces = FakeSpaces()
    prompts = [f"p{i}" for i in range(250)]
    batch = await create_batch(
        db_session,
        prompts=prompts,
        provider="mock",
        model="mock-1",
        spaces=spaces,  # type: ignore[arg-type]
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
        prompts=prompts,
        provider="mock",
        model="mock-1",
        spaces=spaces,  # type: ignore[arg-type]
        chunk_size=5,
        rate_limit_rps=1000,
        max_concurrency=4,
    )
    chunks = await create_chunk_rows(db_session, batch)
    await db_session.commit()

    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(jobs, "get_session_factory", lambda: factory)

    redis = MagicMock()
    redis.enqueue_job = AsyncMock()

    from fakeredis.aioredis import FakeRedis

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
