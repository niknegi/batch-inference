"""Unit / service tests for batch planning and create_batch edge cases."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.batches import create_batch, plan_chunks
from tests.conftest import FakeSpaces


def test_plan_chunks_basic():
    assert plan_chunks(250, 100) == [(0, 0, 100), (1, 100, 100), (2, 200, 50)]


def test_plan_chunks_exact():
    assert plan_chunks(200, 100) == [(0, 0, 100), (1, 100, 100)]


def test_plan_chunks_empty_total():
    assert plan_chunks(0, 100) == []


def test_plan_chunks_single_item():
    assert plan_chunks(1, 100) == [(0, 0, 1)]


def test_plan_chunks_chunk_size_one():
    assert plan_chunks(3, 1) == [(0, 0, 1), (1, 1, 1), (2, 2, 1)]


def test_plan_chunks_invalid_chunk_size():
    with pytest.raises(ValueError, match="chunk_size"):
        plan_chunks(10, 0)
    with pytest.raises(ValueError, match="chunk_size"):
        plan_chunks(10, -5)


def test_plan_chunks_scale_500k():
    chunks = plan_chunks(500_000, 100)
    assert len(chunks) == 5_000
    assert chunks[0] == (0, 0, 100)
    assert chunks[-1] == (4999, 499_900, 100)
    assert sum(c[2] for c in chunks) == 500_000


@pytest.mark.asyncio
async def test_create_batch_empty_prompts_error(db_session: AsyncSession, spaces: FakeSpaces):
    with pytest.raises(ValueError, match="non-empty"):
        await create_batch(
            db_session,
            spaces=spaces,  # type: ignore[arg-type]
            prompts=[],
            provider="mock",
            model="mock-1",
        )


@pytest.mark.asyncio
async def test_create_batch_no_source_error(db_session: AsyncSession, spaces: FakeSpaces):
    with pytest.raises(ValueError, match="one of prompts"):
        await create_batch(
            db_session,
            spaces=spaces,  # type: ignore[arg-type]
            provider="mock",
            model="mock-1",
        )


@pytest.mark.asyncio
async def test_create_batch_empty_raw_ndjson_error(db_session: AsyncSession, spaces: FakeSpaces):
    with pytest.raises(ValueError, match="at least one"):
        await create_batch(
            db_session,
            spaces=spaces,  # type: ignore[arg-type]
            raw_ndjson=b"\n\n",
            provider="mock",
            model="mock-1",
        )
