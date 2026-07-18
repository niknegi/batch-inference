from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update

from app.core.config import get_settings
from app.core.db import get_session_factory
from app.core.logging import get_logger
from app.core.metrics import (
    BATCHES_TOTAL,
    CHUNKS_INFLIGHT,
    INFERENCE_LATENCY,
    INFERENCE_REQUESTS,
)
from app.core.spaces import SpacesClient, chunk_result_key, manifest_key, results_key
from app.models import Batch, BatchChunk, BatchStatus, ChunkStatus, WebhookStatus
from app.providers.base import ErrorKind, InferenceRequest
from app.rate_limit import BatchConcurrencyGate, TokenBucketRateLimiter
from app.services.batches import create_chunk_rows
from app.services.webhooks import (
    build_webhook_payload,
    deliver_webhook,
    webhook_backoff_seconds,
)

logger = get_logger(__name__)


async def reclaim_expired_leases(session) -> int:
    now = datetime.now(UTC)
    result = await session.execute(
        update(BatchChunk)
        .where(
            and_(
                BatchChunk.status == ChunkStatus.leased,
                BatchChunk.leased_until.is_not(None),
                BatchChunk.leased_until < now,
            )
        )
        .values(status=ChunkStatus.pending, leased_until=None)
    )
    return result.rowcount or 0


async def orchestrate_batch(ctx: dict[str, Any], batch_id: str) -> dict[str, Any]:
    settings = get_settings()
    redis = ctx["redis"]

    async with get_session_factory()() as session:
        batch = await session.get(Batch, batch_id)
        if not batch:
            logger.error("orchestrate_missing_batch", batch_id=batch_id)
            return {"ok": False, "error": "not_found"}

        if batch.status == BatchStatus.cancelled:
            return {"ok": True, "status": "cancelled"}

        if batch.status in (BatchStatus.completed, BatchStatus.failed):
            return {"ok": True, "status": batch.status.value}

        batch.status = BatchStatus.running
        batch.started_at = batch.started_at or datetime.now(UTC)
        chunks = await create_chunk_rows(session, batch)
        await reclaim_expired_leases(session)
        await session.commit()

        chunk_ids = [(c.id, c.chunk_index) for c in chunks if c.status != ChunkStatus.succeeded]

    # Enqueue chunk jobs (small payloads only)
    for chunk_db_id, chunk_index in chunk_ids:
        await redis.enqueue_job("process_chunk", batch_id, chunk_db_id, chunk_index)

    # Schedule a finalize poll
    await redis.enqueue_job("check_batch_completion", batch_id, _defer_by=2)

    logger.info(
        "batch_orchestrated",
        batch_id=batch_id,
        chunks=len(chunk_ids),
        worker_concurrency=settings.worker_concurrency,
    )
    return {"ok": True, "chunks_enqueued": len(chunk_ids)}


async def _lease_chunk(session, chunk_id: int, lease_seconds: int) -> BatchChunk | None:
    now = datetime.now(UTC)
    leased_until = now + timedelta(seconds=lease_seconds)
    settings = get_settings()

    chunk = await session.get(BatchChunk, chunk_id)
    if not chunk:
        return None
    if chunk.status == ChunkStatus.succeeded:
        return None
    if chunk.attempts >= settings.chunk_max_attempts and chunk.status == ChunkStatus.failed:
        return None
    if chunk.status == ChunkStatus.leased and chunk.leased_until and chunk.leased_until > now:
        # Another worker holds the lease
        return None

    chunk.status = ChunkStatus.leased
    chunk.leased_until = leased_until
    chunk.attempts += 1
    await session.flush()
    return chunk


async def process_chunk(
    ctx: dict[str, Any], batch_id: str, chunk_id: int, chunk_index: int
) -> dict[str, Any]:
    settings = get_settings()
    spaces: SpacesClient = ctx["spaces"]
    providers = ctx["providers"]
    limiter: TokenBucketRateLimiter = ctx["limiter"]
    gate: BatchConcurrencyGate = ctx["gate"]

    async with get_session_factory()() as session:
        batch = await session.get(Batch, batch_id)
        if not batch:
            return {"ok": False, "error": "batch_not_found"}
        if batch.status in (BatchStatus.cancelled, BatchStatus.failed, BatchStatus.completed):
            return {"ok": True, "skipped": batch.status.value}

        chunk = await _lease_chunk(session, chunk_id, settings.chunk_lease_seconds)
        if not chunk:
            await session.commit()
            return {"ok": True, "skipped": "lease"}
        await session.commit()

        CHUNKS_INFLIGHT.inc()
        try:
            rows = await spaces.read_line_range(batch.prompts_key, chunk.offset, chunk.limit)
            provider = providers.get(batch.provider)
            sem = gate.get(batch_id, batch.max_concurrency)

            async def run_one(item: dict[str, Any]) -> dict[str, Any]:
                prompt = item.get("prompt") or item.get("input") or ""
                index = int(item.get("index", chunk.offset))
                req = InferenceRequest(
                    prompt=str(prompt),
                    model=batch.model,
                    provider=batch.provider,
                    index=index,
                    metadata={k: v for k, v in item.items() if k not in ("prompt", "input")},
                    system=item.get("system"),
                    temperature=item.get("temperature"),
                    max_tokens=item.get("max_tokens"),
                )

                async with get_session_factory()() as s2:
                    b2 = await s2.get(Batch, batch_id)
                    if b2 and b2.status == BatchStatus.cancelled:
                        return {
                            "index": index,
                            "ok": False,
                            "error": "cancelled",
                            "cancelled": True,
                        }

                async with sem:
                    bucket = provider.rate_limit_key(req)
                    await limiter.acquire(
                        bucket, rate=batch.rate_limit_rps, capacity=batch.rate_limit_rps
                    )

                    last = None
                    for attempt in range(3):
                        t0 = time.perf_counter()
                        last = await provider.infer(req)
                        INFERENCE_LATENCY.labels(
                            provider=batch.provider, model=batch.model
                        ).observe(time.perf_counter() - t0)
                        if last.ok:
                            INFERENCE_REQUESTS.labels(
                                provider=batch.provider, model=batch.model, status="ok"
                            ).inc()
                            break
                        status = (
                            "retryable" if last.error_kind == ErrorKind.retryable else "terminal"
                        )
                        INFERENCE_REQUESTS.labels(
                            provider=batch.provider, model=batch.model, status=status
                        ).inc()
                        if last.error_kind != ErrorKind.retryable:
                            break
                        retry_after = last.raw.get("retry_after") if last.raw else None
                        if retry_after:
                            try:
                                await limiter.pause(bucket, float(retry_after))
                            except Exception:  # noqa: BLE001
                                pass
                        await asyncio.sleep(min(2**attempt, 8))

                    assert last is not None
                    if last.ok:
                        return {
                            "index": index,
                            "ok": True,
                            "output": last.output,
                            "usage": last.usage,
                        }
                    return {
                        "index": index,
                        "ok": False,
                        "error": last.error,
                        "error_kind": last.error_kind.value if last.error_kind else None,
                    }

            outcomes = await asyncio.gather(*[run_one(item) for item in rows])
            if any(o.get("cancelled") for o in outcomes):
                async with get_session_factory()() as session:
                    chunk = await session.get(BatchChunk, chunk_id)
                    if chunk:
                        chunk.status = ChunkStatus.pending
                        chunk.leased_until = None
                        chunk.error = "cancelled"
                        await session.commit()
                return {"ok": True, "cancelled": True}

            results = sorted(outcomes, key=lambda r: r["index"])
            ok_count = sum(1 for r in results if r.get("ok"))
            fail_count = sum(1 for r in results if not r.get("ok"))
            rkey = chunk_result_key(batch_id, chunk_index)
            await spaces.write_chunk_results(rkey, results)

            async with get_session_factory()() as session:
                chunk = await session.get(BatchChunk, chunk_id)
                batch = await session.get(Batch, batch_id)
                if not chunk or not batch:
                    return {"ok": False, "error": "missing"}

                chunk.status = ChunkStatus.succeeded
                chunk.result_key = rkey
                chunk.ok_count = ok_count
                chunk.fail_count = fail_count
                chunk.leased_until = None
                chunk.error = None
                await session.flush()

                # Recompute counters from succeeded chunks for crash safety
                totals = await session.execute(
                    select(
                        func.coalesce(func.sum(BatchChunk.ok_count), 0),
                        func.coalesce(func.sum(BatchChunk.fail_count), 0),
                    ).where(
                        and_(
                            BatchChunk.batch_id == batch_id,
                            BatchChunk.status == ChunkStatus.succeeded,
                        )
                    )
                )
                ok_sum, fail_sum = totals.one()
                batch.completed_items = int(ok_sum) + int(fail_sum)
                batch.failed_items = int(fail_sum)
                await session.commit()

            # Nudge completion check
            await ctx["redis"].enqueue_job("check_batch_completion", batch_id)
            logger.info(
                "chunk_succeeded",
                batch_id=batch_id,
                chunk_index=chunk_index,
                ok=ok_count,
                fail=fail_count,
            )
            return {"ok": True, "ok_count": ok_count, "fail_count": fail_count}

        except Exception as exc:  # noqa: BLE001
            logger.exception("chunk_failed", batch_id=batch_id, chunk_index=chunk_index)
            async with get_session_factory()() as session:
                chunk = await session.get(BatchChunk, chunk_id)
                batch = await session.get(Batch, batch_id)
                if chunk:
                    chunk.error = str(exc)[:2000]
                    chunk.leased_until = None
                    if chunk.attempts >= settings.chunk_max_attempts:
                        chunk.status = ChunkStatus.failed
                        if batch and batch.status == BatchStatus.running:
                            batch.status = BatchStatus.failed
                            batch.error = f"chunk {chunk_index} exhausted retries: {exc}"
                            batch.completed_at = datetime.now(UTC)
                            BATCHES_TOTAL.labels(status="failed").inc()
                            await session.commit()
                            await ctx["redis"].enqueue_job(
                                "deliver_batch_webhook", batch_id, "batch.failed"
                            )
                    else:
                        chunk.status = ChunkStatus.pending
                        await session.commit()
                        # Re-enqueue
                        await ctx["redis"].enqueue_job(
                            "process_chunk", batch_id, chunk_id, chunk_index, _defer_by=2
                        )
            return {"ok": False, "error": str(exc)}
        finally:
            CHUNKS_INFLIGHT.dec()


async def check_batch_completion(ctx: dict[str, Any], batch_id: str) -> dict[str, Any]:
    async with get_session_factory()() as session:
        batch = await session.get(Batch, batch_id)
        if not batch:
            return {"ok": False}
        if batch.status in (BatchStatus.completed, BatchStatus.failed, BatchStatus.cancelled):
            return {"ok": True, "status": batch.status.value}

        await reclaim_expired_leases(session)

        counts = await session.execute(
            select(BatchChunk.status, func.count())
            .where(BatchChunk.batch_id == batch_id)
            .group_by(BatchChunk.status)
        )
        by_status = {row[0]: row[1] for row in counts.all()}
        total_chunks = sum(by_status.values())
        succeeded = by_status.get(ChunkStatus.succeeded, 0)
        failed = by_status.get(ChunkStatus.failed, 0)
        pendingish = by_status.get(ChunkStatus.pending, 0) + by_status.get(ChunkStatus.leased, 0)

        if failed > 0 and pendingish == 0 and succeeded + failed == total_chunks:
            batch.status = BatchStatus.failed
            batch.error = batch.error or f"{failed} chunk(s) failed"
            batch.completed_at = datetime.now(UTC)
            await session.commit()
            BATCHES_TOTAL.labels(status="failed").inc()
            await ctx["redis"].enqueue_job("deliver_batch_webhook", batch_id, "batch.failed")
            return {"ok": True, "status": "failed"}

        if succeeded == total_chunks and total_chunks > 0:
            await session.commit()
            await ctx["redis"].enqueue_job("finalize_batch", batch_id)
            return {"ok": True, "status": "finalizing"}

        await session.commit()

        if pendingish > 0:
            # Re-enqueue pending chunks that may have been lost
            pending = (
                await session.scalars(
                    select(BatchChunk).where(
                        and_(
                            BatchChunk.batch_id == batch_id,
                            or_(
                                BatchChunk.status == ChunkStatus.pending,
                                and_(
                                    BatchChunk.status == ChunkStatus.leased,
                                    BatchChunk.leased_until < datetime.now(UTC),
                                ),
                            ),
                        )
                    )
                )
            ).all()
            for c in pending:
                if c.status == ChunkStatus.leased:
                    c.status = ChunkStatus.pending
                    c.leased_until = None
                await ctx["redis"].enqueue_job("process_chunk", batch_id, c.id, c.chunk_index)
            await session.commit()
            await ctx["redis"].enqueue_job("check_batch_completion", batch_id, _defer_by=5)

        return {
            "ok": True,
            "succeeded": succeeded,
            "failed": failed,
            "pending": pendingish,
        }


async def finalize_batch(ctx: dict[str, Any], batch_id: str) -> dict[str, Any]:
    spaces: SpacesClient = ctx["spaces"]

    async with get_session_factory()() as session:
        batch = await session.get(Batch, batch_id)
        if not batch:
            return {"ok": False}
        if batch.status == BatchStatus.completed:
            return {"ok": True, "already": True}
        if batch.status == BatchStatus.cancelled:
            return {"ok": True, "cancelled": True}

        chunks = (
            await session.scalars(
                select(BatchChunk)
                .where(BatchChunk.batch_id == batch_id)
                .order_by(BatchChunk.chunk_index)
            )
        ).all()
        if not chunks or any(c.status != ChunkStatus.succeeded for c in chunks):
            await session.commit()
            await ctx["redis"].enqueue_job("check_batch_completion", batch_id, _defer_by=2)
            return {"ok": False, "error": "chunks_incomplete"}

        chunk_keys = [c.result_key for c in chunks if c.result_key]
        rkey = results_key(batch_id)
        mkey = manifest_key(batch_id)
        await spaces.concatenate_chunks(chunk_keys, rkey)
        await spaces.put_json(
            mkey,
            {
                "batch_id": batch_id,
                "total_items": batch.total_items,
                "completed_items": batch.completed_items,
                "failed_items": batch.failed_items,
                "chunks": [
                    {
                        "chunk_index": c.chunk_index,
                        "result_key": c.result_key,
                        "ok_count": c.ok_count,
                        "fail_count": c.fail_count,
                    }
                    for c in chunks
                ],
                "results_key": rkey,
            },
        )

        batch.results_key = rkey
        batch.manifest_key = mkey
        batch.status = BatchStatus.completed
        batch.completed_at = datetime.now(UTC)
        await session.commit()
        BATCHES_TOTAL.labels(status="completed").inc()

    await ctx["redis"].enqueue_job("deliver_batch_webhook", batch_id, "batch.completed")
    logger.info("batch_finalized", batch_id=batch_id)
    return {"ok": True, "results_key": rkey}


async def deliver_batch_webhook(ctx: dict[str, Any], batch_id: str, event: str) -> dict[str, Any]:
    settings = get_settings()
    spaces: SpacesClient = ctx["spaces"]

    async with get_session_factory()() as session:
        batch = await session.get(Batch, batch_id)
        if not batch or not batch.webhook_url:
            return {"ok": True, "skipped": True}

        result_url = None
        if batch.results_key:
            try:
                result_url = await spaces.generate_presigned_url(batch.results_key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("presign_failed", error=str(exc))

        payload = build_webhook_payload(
            event=event,
            batch_id=batch_id,
            status=batch.status.value,
            result_url=result_url,
            stats={
                "total_items": batch.total_items,
                "completed_items": batch.completed_items,
                "failed_items": batch.failed_items,
            },
            completed_at=batch.completed_at,
        )

        ok, err = await deliver_webhook(
            url=batch.webhook_url,
            secret=batch.webhook_secret,
            payload=payload,
        )
        batch.webhook_attempts += 1
        if ok:
            batch.webhook_status = WebhookStatus.delivered
            await session.commit()
            return {"ok": True, "delivered": True}

        if batch.webhook_attempts >= settings.webhook_max_attempts:
            batch.webhook_status = WebhookStatus.dead
            await session.commit()
            logger.error("webhook_dead", batch_id=batch_id, error=err)
            return {"ok": False, "dead": True, "error": err}

        await session.commit()
        delay = webhook_backoff_seconds(batch.webhook_attempts)
        await ctx["redis"].enqueue_job("deliver_batch_webhook", batch_id, event, _defer_by=delay)
        return {"ok": False, "retry_in": delay, "error": err}


async def reclaim_leases_cron(ctx: dict[str, Any]) -> dict[str, Any]:
    async with get_session_factory()() as session:
        n = await reclaim_expired_leases(session)
        await session.commit()
    if n:
        logger.info("reclaimed_leases", count=n)
    return {"reclaimed": n}
