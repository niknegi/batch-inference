from __future__ import annotations

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import PlainTextResponse, RedirectResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.api.auth import require_api_key
from app.api.schemas import (
    BatchCreateRequest,
    BatchCreateResponse,
    BatchProgress,
    BatchResponse,
    HealthResponse,
    WebhookTestRequest,
    WebhookTestResponse,
)
from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.spaces import SpacesClient
from app.models import Batch
from app.services.batches import cancel_batch, create_batch, get_batch
from app.services.webhooks import build_webhook_payload, deliver_webhook

router = APIRouter()


def _to_response(batch: Batch, result_url: str | None = None) -> BatchResponse:
    total = batch.total_items or 1
    return BatchResponse(
        id=batch.id,
        status=batch.status.value,
        provider=batch.provider,
        model=batch.model,
        progress=BatchProgress(
            total_items=batch.total_items,
            completed_items=batch.completed_items,
            failed_items=batch.failed_items,
            fraction=round(batch.completed_items / total, 6),
        ),
        chunk_size=batch.chunk_size,
        rate_limit_rps=batch.rate_limit_rps,
        max_concurrency=batch.max_concurrency,
        prompts_key=batch.prompts_key,
        results_key=batch.results_key,
        manifest_key=batch.manifest_key,
        result_url=result_url,
        webhook_url=batch.webhook_url,
        webhook_status=batch.webhook_status.value,
        error=batch.error,
        created_at=batch.created_at,
        started_at=batch.started_at,
        completed_at=batch.completed_at,
    )


async def get_arq(request: Request) -> ArqRedis:
    return request.app.state.arq


async def get_spaces(request: Request) -> SpacesClient:
    return request.app.state.spaces


@router.get("/health", response_model=HealthResponse, tags=["health"])
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get("/metrics", tags=["health"])
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest().decode("utf-8"), media_type=CONTENT_TYPE_LATEST)


@router.post(
    "/v1/batches",
    response_model=BatchCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["batches"],
)
async def create_batch_endpoint(
    body: BatchCreateRequest,
    session: AsyncSession = Depends(get_db),
    _: str = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
    spaces: SpacesClient = Depends(get_spaces),
    arq: ArqRedis = Depends(get_arq),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> BatchCreateResponse:
    try:
        batch = await create_batch(
            session,
            prompts=body.prompts,
            provider=body.provider,
            model=body.model,
            spaces=spaces,
            webhook_url=body.webhook_url,
            webhook_secret=body.webhook_secret,
            chunk_size=body.chunk_size,
            rate_limit_rps=body.rate_limit_rps,
            max_concurrency=body.max_concurrency,
            idempotency_key=idempotency_key,
            settings=settings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await session.commit()
    await arq.enqueue_job("orchestrate_batch", batch.id)
    return BatchCreateResponse(
        id=batch.id,
        status=batch.status.value,
        total_items=batch.total_items,
        chunk_size=batch.chunk_size,
    )


@router.get("/v1/batches/{batch_id}", response_model=BatchResponse, tags=["batches"])
async def get_batch_endpoint(
    batch_id: str,
    session: AsyncSession = Depends(get_db),
    _: str = Depends(require_api_key),
    spaces: SpacesClient = Depends(get_spaces),
) -> BatchResponse:
    batch = await get_batch(session, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    result_url = None
    if batch.results_key:
        try:
            result_url = await spaces.generate_presigned_url(batch.results_key)
        except Exception:  # noqa: BLE001
            result_url = None
    return _to_response(batch, result_url=result_url)


@router.get("/v1/batches/{batch_id}/results", tags=["batches"])
async def get_batch_results(
    batch_id: str,
    session: AsyncSession = Depends(get_db),
    _: str = Depends(require_api_key),
    spaces: SpacesClient = Depends(get_spaces),
    redirect: bool = True,
):
    batch = await get_batch(session, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    if not batch.results_key:
        raise HTTPException(status_code=409, detail="Results not ready")
    url = await spaces.generate_presigned_url(batch.results_key)
    if redirect:
        return RedirectResponse(url=url, status_code=302)
    return {"result_url": url, "results_key": batch.results_key, "manifest_key": batch.manifest_key}


@router.post("/v1/batches/{batch_id}/cancel", response_model=BatchResponse, tags=["batches"])
async def cancel_batch_endpoint(
    batch_id: str,
    session: AsyncSession = Depends(get_db),
    _: str = Depends(require_api_key),
) -> BatchResponse:
    batch = await get_batch(session, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    batch = await cancel_batch(session, batch)
    await session.commit()
    return _to_response(batch)


@router.post("/v1/webhooks/test", response_model=WebhookTestResponse, tags=["webhooks"])
async def test_webhook(
    body: WebhookTestRequest,
    _: str = Depends(require_api_key),
) -> WebhookTestResponse:
    payload = build_webhook_payload(
        event="webhook.test",
        batch_id="test",
        status="test",
        result_url=None,
        stats={},
        completed_at=None,
    )
    ok, err = await deliver_webhook(url=str(body.url), secret=body.secret, payload=payload)
    return WebhookTestResponse(ok=ok, error=err)
