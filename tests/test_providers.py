"""Unit tests for inference providers (mock + HTTP error classification)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import Settings
from app.providers import MockProvider, OpenAIProvider, build_providers
from app.providers.base import ErrorKind, InferenceRequest


def _req(prompt: str = "hello", index: int = 0) -> InferenceRequest:
    return InferenceRequest(prompt=prompt, model="gpt-4o-mini", provider="openai", index=index)


@pytest.mark.asyncio
async def test_mock_provider_success():
    p = MockProvider(latency_ms=0)
    result = await p.infer(
        InferenceRequest(prompt="hello", model="mock-1", provider="mock", index=0)
    )
    assert result.ok
    assert result.output == "echo:hello"
    await p.aclose()


@pytest.mark.asyncio
async def test_mock_provider_terminal_failure():
    p = MockProvider(latency_ms=0)
    result = await p.infer(
        InferenceRequest(
            prompt="__fail_terminal__ boom", model="mock-1", provider="mock", index=1
        )
    )
    assert not result.ok
    assert result.error_kind == ErrorKind.terminal
    await p.aclose()


@pytest.mark.asyncio
async def test_mock_provider_retryable_failure():
    p = MockProvider(latency_ms=0)
    result = await p.infer(
        InferenceRequest(
            prompt="__fail_retry__ boom", model="mock-1", provider="mock", index=2
        )
    )
    assert not result.ok
    assert result.error_kind == ErrorKind.retryable
    await p.aclose()


@pytest.mark.asyncio
async def test_openai_http_429_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited", headers={"Retry-After": "2"})

    provider = OpenAIProvider(api_key="sk-test")
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        timeout=httpx.Timeout(30.0),
    )
    result = await provider.infer(_req())
    await provider.aclose()

    assert not result.ok
    assert result.error_kind == ErrorKind.retryable
    assert "429" in (result.error or "")
    assert result.raw.get("retry_after") == "2"


@pytest.mark.asyncio
async def test_openai_http_400_is_terminal():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request")

    provider = OpenAIProvider(api_key="sk-test")
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        timeout=httpx.Timeout(30.0),
    )
    result = await provider.infer(_req())
    await provider.aclose()

    assert not result.ok
    assert result.error_kind == ErrorKind.terminal
    assert "400" in (result.error or "")


@pytest.mark.asyncio
async def test_openai_success_path():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["model"] == "gpt-4o-mini"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "hi there"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2},
            },
        )

    provider = OpenAIProvider(api_key="sk-test")
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        timeout=httpx.Timeout(30.0),
    )
    result = await provider.infer(_req())
    await provider.aclose()

    assert result.ok
    assert result.output == "hi there"


def test_build_providers_always_has_mock_when_enabled():
    providers = build_providers(Settings(MOCK_PROVIDER=True))
    assert "mock" in providers
