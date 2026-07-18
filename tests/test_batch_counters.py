"""Progress recompute + retry_count behavior."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Batch, BatchChunk, ChunkStatus
from app.services.batches import create_batch, create_chunk_rows
from app.workers.jobs import _lease_chunk, _recompute_batch_counters
from tests.conftest import FakeSpaces


@pytest.mark.asyncio
async def test_recompute_progress_idempotent_on_repeated_success(db_session: AsyncSession):
    """Simulates success-path recompute running twice for the same chunk (retry race)."""
    spaces = FakeSpaces()
    batch = await create_batch(
        db_session,
        spaces=spaces,  # type: ignore[arg-type]
        prompts=[f"p{i}" for i in range(100)],
        provider="mock",
        model="mock-1",
        chunk_size=100,
    )
    chunks = await create_chunk_rows(db_session, batch)
    await db_session.commit()

    chunk = chunks[0]
    chunk.status = ChunkStatus.succeeded
    chunk.ok_count = 66
    chunk.fail_count = 34
    chunk.attempts = 1
    # Inflate counters as if an old increment path ran 1.5x
    batch.completed_items = 150
    batch.failed_items = 50
    await db_session.flush()

    await _recompute_batch_counters(db_session, batch.id)
    await db_session.commit()

    await db_session.refresh(batch)
    assert batch.completed_items == 100
    assert batch.failed_items == 34
    assert batch.retry_count == 0

    # Second success-path recompute must not grow counters
    await _recompute_batch_counters(db_session, batch.id)
    await db_session.commit()
    await db_session.refresh(batch)
    assert batch.completed_items == 100
    assert batch.failed_items == 34
    assert batch.retry_count == 0


@pytest.mark.asyncio
async def test_lease_retry_increments_batch_retry_count(db_session: AsyncSession, monkeypatch):
    import app.workers.jobs as jobs

    spaces = FakeSpaces()
    batch = await create_batch(
        db_session,
        spaces=spaces,  # type: ignore[arg-type]
        prompts=["a", "b", "c"],
        provider="mock",
        model="mock-1",
        chunk_size=10,
    )
    chunks = await create_chunk_rows(db_session, batch)
    await db_session.commit()

    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(jobs, "get_session_factory", lambda: factory)

    chunk_id = chunks[0].id

    async with factory() as s:
        leased = await _lease_chunk(s, chunk_id, lease_seconds=60)
        assert leased is not None
        assert leased.attempts == 1
        await s.commit()

    async with factory() as s:
        b = await s.get(Batch, batch.id)
        assert b is not None
        assert b.retry_count == 0
        c = await s.get(BatchChunk, chunk_id)
        assert c is not None
        # Simulate reclaim / failure re-queue
        c.status = ChunkStatus.pending
        c.leased_until = None
        await s.commit()

    async with factory() as s:
        leased = await _lease_chunk(s, chunk_id, lease_seconds=60)
        assert leased is not None
        assert leased.attempts == 2
        await s.commit()

    async with factory() as s:
        b = await s.get(Batch, batch.id)
        assert b is not None
        assert b.retry_count == 1

    # Recompute from chunks stays consistent (does not double-count live bumps)
    async with factory() as s:
        c = await s.get(BatchChunk, chunk_id)
        assert c is not None
        c.status = ChunkStatus.succeeded
        c.ok_count = 3
        c.fail_count = 0
        await s.flush()
        await _recompute_batch_counters(s, batch.id)
        await s.commit()

    async with factory() as s:
        b = await s.get(Batch, batch.id)
        assert b is not None
        assert b.completed_items == 3
        assert b.failed_items == 0
        assert b.retry_count == 1
