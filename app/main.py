from __future__ import annotations

from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI

from app.api.routes import router
from app.core.config import get_settings
from app.core.db import dispose_engine, get_engine
from app.core.logging import setup_logging
from app.core.spaces import SpacesClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    get_engine()
    spaces = SpacesClient(settings)
    await spaces.ensure_bucket()
    arq = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    app.state.spaces = spaces
    app.state.arq = arq
    yield
    await arq.close()
    await dispose_engine()


app = FastAPI(
    title="Batch Inference Service",
    version="0.1.0",
    description="Chunked, rate-limited multi-provider batch inference with Spaces checkpoints and webhooks.",
    lifespan=lifespan,
)
app.include_router(router)


def run_api() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
