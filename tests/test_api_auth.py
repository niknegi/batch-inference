"""API auth and batch create tests via FastAPI TestClient (dependency overrides)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.routes import get_arq, get_spaces, router
from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.models import Base
from tests.conftest import FakeSpaces


@pytest.fixture
async def api_env():
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

    with TestClient(app) as client:
        yield client, spaces, arq, settings, factory
    await engine.dispose()


def test_health_is_free(api_env):
    client, *_ = api_env
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_create_batch_unauthorized_without_key(api_env):
    client, *_ = api_env
    resp = client.post(
        "/v1/batches",
        json={"prompts": ["a"], "provider": "mock", "model": "mock-1"},
    )
    assert resp.status_code == 401


def test_create_batch_accepted_with_key(api_env):
    client, spaces, arq, *_ = api_env
    resp = client.post(
        "/v1/batches",
        headers={"Authorization": "Bearer test-api-key"},
        json={"prompts": ["hello", "world"], "provider": "mock", "model": "mock-1"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert "id" in body
    assert body["total_items"] == 2
    assert body["status"] == "pending"
    arq.enqueue_job.assert_called()
    assert any(k.endswith("/prompts.ndjson") for k in spaces.objects)


def test_create_batch_invalid_key(api_env):
    client, *_ = api_env
    resp = client.post(
        "/v1/batches",
        headers={"Authorization": "Bearer wrong"},
        json={"prompts": ["a"], "provider": "mock", "model": "mock-1"},
    )
    assert resp.status_code == 401


def test_list_batches_unauthorized_without_key(api_env):
    client, *_ = api_env
    resp = client.get("/v1/batches")
    assert resp.status_code == 401


def test_list_batches_returns_newest_first(api_env):
    client, *_ = api_env
    headers = {"Authorization": "Bearer test-api-key"}
    first = client.post(
        "/v1/batches",
        headers=headers,
        json={"prompts": ["one"], "provider": "mock", "model": "mock-1"},
    )
    second = client.post(
        "/v1/batches",
        headers=headers,
        json={"prompts": ["two"], "provider": "mock", "model": "mock-1"},
    )
    assert first.status_code == 202
    assert second.status_code == 202

    resp = client.get("/v1/batches", headers=headers, params={"limit": 10, "offset": 0})
    assert resp.status_code == 200
    body = resp.json()
    assert body["limit"] == 10
    assert body["offset"] == 0
    assert len(body["items"]) >= 2
    ids = [item["id"] for item in body["items"]]
    assert second.json()["id"] in ids
    assert first.json()["id"] in ids
    assert ids.index(second.json()["id"]) < ids.index(first.json()["id"])
    assert all("status" in item and "progress" in item for item in body["items"])
