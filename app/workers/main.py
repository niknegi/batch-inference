from __future__ import annotations

from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.db import dispose_engine, get_engine
from app.core.logging import setup_logging
from app.core.spaces import SpacesClient
from app.providers import ProviderRegistry, build_providers
from app.rate_limit import BatchConcurrencyGate, TokenBucketRateLimiter
from app.workers.jobs import (
    check_batch_completion,
    deliver_batch_webhook,
    finalize_batch,
    orchestrate_batch,
    process_chunk,
    reclaim_leases_cron,
)


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


async def on_startup(ctx: dict) -> None:
    setup_logging()
    settings = get_settings()
    get_engine()
    spaces = SpacesClient(settings)
    await spaces.ensure_bucket()
    ctx["spaces"] = spaces
    ctx["providers"] = ProviderRegistry(build_providers(settings))
    ctx["limiter"] = TokenBucketRateLimiter(ctx["redis"])
    ctx["gate"] = BatchConcurrencyGate()


async def on_shutdown(ctx: dict) -> None:
    providers = ctx.get("providers")
    if providers:
        await providers.aclose()
    await dispose_engine()


class WorkerSettings:
    functions = [
        orchestrate_batch,
        process_chunk,
        check_batch_completion,
        finalize_batch,
        deliver_batch_webhook,
        reclaim_leases_cron,
    ]
    cron_jobs = [cron(reclaim_leases_cron, second={0})]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = _redis_settings()
    max_jobs = get_settings().worker_concurrency
    job_timeout = 600
    keep_result = 3600


def run_worker() -> None:
    import sys

    from arq.cli import cli

    sys.argv = ["arq", "app.workers.main.WorkerSettings"]
    cli()
