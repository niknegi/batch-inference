from __future__ import annotations

import math
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.spaces import SpacesClient, batch_prefix, prompts_key
from app.models import Batch, BatchChunk, BatchStatus, WebhookStatus
from app.services.routing import resolve_model

logger = get_logger(__name__)


def new_batch_id() -> str:
    return str(ULID())


async def create_batch(
    session: AsyncSession,
    *,
    spaces: SpacesClient,
    prompts: Sequence[str | dict[str, Any]] | None = None,
    raw_ndjson: bytes | None = None,
    source_key: str | None = None,
    prompts_url: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    cost_preference: str | None = None,
    webhook_url: str | None = None,
    webhook_secret: str | None = None,
    chunk_size: int | None = None,
    rate_limit_rps: float | None = None,
    max_concurrency: int | None = None,
    idempotency_key: str | None = None,
    settings: Settings | None = None,
) -> Batch:
    settings = settings or get_settings()

    if idempotency_key:
        existing = await session.scalar(
            select(Batch).where(Batch.idempotency_key == idempotency_key)
        )
        if existing:
            return existing

    sources = sum(
        1
        for s in (prompts is not None, raw_ndjson is not None, source_key, prompts_url)
        if s
    )
    if sources == 0:
        raise ValueError(
            "one of prompts, raw_ndjson, source_key, or prompts_url is required"
        )
    if prompts is not None and not prompts:
        raise ValueError("prompts must be non-empty")

    choice = resolve_model(
        provider=provider,
        model=model,
        cost_preference=cost_preference,
        settings=settings,
    )

    batch_id = new_batch_id()
    pkey = prompts_key(batch_id)

    if prompts is not None:
        total = await spaces.upload_prompts_ndjson(pkey, prompts)
    elif raw_ndjson is not None:
        total = await spaces.upload_raw_ndjson(pkey, raw_ndjson)
    elif source_key:
        await spaces.copy_key(source_key, pkey)
        total = await spaces.count_ndjson_lines(pkey)
    else:
        assert prompts_url is not None
        total = await spaces.download_url_to_key(prompts_url, pkey)

    if total < 1:
        raise ValueError("input must contain at least one prompt line")

    secret = webhook_secret
    if webhook_url and not secret:
        secret = secrets.token_urlsafe(32)

    batch = Batch(
        id=batch_id,
        status=BatchStatus.pending,
        provider=choice.provider,
        model=choice.model,
        total_items=total,
        chunk_size=chunk_size or settings.default_chunk_size,
        completed_items=0,
        failed_items=0,
        prompts_key=pkey,
        result_prefix=batch_prefix(batch_id),
        webhook_url=webhook_url,
        webhook_secret=secret,
        webhook_status=WebhookStatus.pending if webhook_url else WebhookStatus.none,
        rate_limit_rps=rate_limit_rps or settings.default_rate_limit_rps,
        max_concurrency=max_concurrency or settings.default_max_concurrency,
        idempotency_key=idempotency_key,
    )
    session.add(batch)
    await session.flush()
    logger.info(
        "batch_created",
        batch_id=batch_id,
        total_items=total,
        provider=choice.provider,
        model=choice.model,
        cost_tier=choice.cost_tier,
        reason=choice.reason,
    )
    return batch


async def get_batch(session: AsyncSession, batch_id: str) -> Batch | None:
    return await session.get(Batch, batch_id)


async def cancel_batch(session: AsyncSession, batch: Batch) -> Batch:
    if batch.status in (BatchStatus.completed, BatchStatus.failed, BatchStatus.cancelled):
        return batch
    batch.status = BatchStatus.cancelled
    batch.completed_at = datetime.now(UTC)
    await session.flush()
    return batch


def plan_chunks(total_items: int, chunk_size: int) -> list[tuple[int, int, int]]:
    """Return list of (chunk_index, offset, limit)."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    n = math.ceil(total_items / chunk_size) if total_items else 0
    chunks: list[tuple[int, int, int]] = []
    for i in range(n):
        offset = i * chunk_size
        limit = min(chunk_size, total_items - offset)
        chunks.append((i, offset, limit))
    return chunks


async def create_chunk_rows(session: AsyncSession, batch: Batch) -> list[BatchChunk]:
    existing = (
        await session.scalars(select(BatchChunk).where(BatchChunk.batch_id == batch.id))
    ).all()
    if existing:
        return list(existing)

    rows: list[BatchChunk] = []
    for chunk_index, offset, limit in plan_chunks(batch.total_items, batch.chunk_size):
        row = BatchChunk(
            batch_id=batch.id,
            chunk_index=chunk_index,
            offset=offset,
            limit=limit,
        )
        session.add(row)
        rows.append(row)
    await session.flush()
    return rows
