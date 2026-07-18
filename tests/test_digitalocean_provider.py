"""Tests for DigitalOcean Inference provider (mocked HTTP)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import Settings
from app.providers import DigitalOceanInferenceProvider, build_providers
from app.providers.base import InferenceRequest


@pytest.mark.asyncio
async def test_digitalocean_chat_completions():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": "llama3.3-70b-instruct",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "A black hole forms when a massive star collapses.",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 12, "total_tokens": 22},
            },
        )

    transport = httpx.MockTransport(handler)
    provider = DigitalOceanInferenceProvider(api_key="test-do-key")
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        transport=transport,
        base_url="https://inference.do-ai.run/v1",
        timeout=httpx.Timeout(30.0),
    )

    result = await provider.infer(
        InferenceRequest(
            prompt="Explain how a black hole is formed.",
            model="llama3.3-70b-instruct",
            provider="digitalocean",
            index=0,
        )
    )
    await provider.aclose()

    assert result.ok
    assert "black hole" in (result.output or "").lower()
    assert captured["authorization"] == "Bearer test-do-key"
    assert captured["url"].endswith("/chat/completions")
    assert captured["body"]["model"] == "llama3.3-70b-instruct"
    assert captured["body"]["messages"][-1]["content"] == "Explain how a black hole is formed."


def test_build_providers_registers_digitalocean():
    settings = Settings(
        MOCK_PROVIDER=True,
        DO_INFERENCE_API_KEY="do-key",
    )
    providers = build_providers(settings)
    assert "mock" in providers
    assert "digitalocean" in providers
