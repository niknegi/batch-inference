from __future__ import annotations

import asyncio
import hashlib

import httpx

from app.core.config import Settings
from app.providers.base import ErrorKind, InferenceRequest, InferenceResult


def _classify_status(status: int) -> ErrorKind:
    if status == 429 or status >= 500:
        return ErrorKind.retryable
    return ErrorKind.terminal


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))

    def rate_limit_key(self, request: InferenceRequest) -> str:
        key_hash = hashlib.sha256(self.api_key.encode()).hexdigest()[:12]
        return f"{self.name}:{request.model}:{key_hash}"

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        messages = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})
        payload: dict = {"model": request.model, "messages": messages}
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens

        try:
            resp = await self._client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            return InferenceResult(
                index=request.index,
                ok=False,
                error=str(exc),
                error_kind=ErrorKind.retryable,
            )

        if resp.status_code >= 400:
            kind = _classify_status(resp.status_code)
            retry_after = resp.headers.get("Retry-After")
            return InferenceResult(
                index=request.index,
                ok=False,
                error=f"HTTP {resp.status_code}: {resp.text[:500]}",
                error_kind=kind,
                raw={"status": resp.status_code, "retry_after": retry_after},
            )

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return InferenceResult(
            index=request.index,
            ok=True,
            output=content,
            usage=data.get("usage") or {},
            raw=data,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, base_url: str = "https://api.anthropic.com") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))

    def rate_limit_key(self, request: InferenceRequest) -> str:
        key_hash = hashlib.sha256(self.api_key.encode()).hexdigest()[:12]
        return f"{self.name}:{request.model}:{key_hash}"

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        payload: dict = {
            "model": request.model,
            "max_tokens": request.max_tokens or 1024,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        if request.system:
            payload["system"] = request.system
        if request.temperature is not None:
            payload["temperature"] = request.temperature

        try:
            resp = await self._client.post(
                f"{self.base_url}/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            return InferenceResult(
                index=request.index,
                ok=False,
                error=str(exc),
                error_kind=ErrorKind.retryable,
            )

        if resp.status_code >= 400:
            kind = _classify_status(resp.status_code)
            return InferenceResult(
                index=request.index,
                ok=False,
                error=f"HTTP {resp.status_code}: {resp.text[:500]}",
                error_kind=kind,
                raw={"status": resp.status_code, "retry_after": resp.headers.get("Retry-After")},
            )

        data = resp.json()
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        return InferenceResult(
            index=request.index,
            ok=True,
            output=text,
            usage=data.get("usage") or {},
            raw=data,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class OpenAICompatibleProvider(OpenAIProvider):
    name = "openai_compatible"

    def __init__(self, api_key: str, base_url: str) -> None:
        super().__init__(api_key=api_key or "none", base_url=base_url)
        self.name = "openai_compatible"


class MockProvider:
    """Deterministic local provider for tests and local docker."""

    name = "mock"

    def __init__(self, latency_ms: float = 5.0) -> None:
        self.latency_ms = latency_ms

    def rate_limit_key(self, request: InferenceRequest) -> str:
        return f"mock:{request.model}:local"

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        await asyncio.sleep(self.latency_ms / 1000.0)
        if request.prompt.startswith("__fail_retry__"):
            return InferenceResult(
                index=request.index,
                ok=False,
                error="forced retryable failure",
                error_kind=ErrorKind.retryable,
            )
        if request.prompt.startswith("__fail_terminal__"):
            return InferenceResult(
                index=request.index,
                ok=False,
                error="forced terminal failure",
                error_kind=ErrorKind.terminal,
            )
        return InferenceResult(
            index=request.index,
            ok=True,
            output=f"echo:{request.prompt}",
            usage={"prompt_tokens": len(request.prompt.split()), "completion_tokens": 1},
        )

    async def aclose(self) -> None:
        return None


def build_providers(settings: Settings) -> dict[str, object]:
    providers: dict[str, object] = {}
    if settings.mock_provider:
        providers["mock"] = MockProvider()
    if settings.openai_api_key:
        providers["openai"] = OpenAIProvider(settings.openai_api_key)
    if settings.anthropic_api_key:
        providers["anthropic"] = AnthropicProvider(settings.anthropic_api_key)
    if settings.openai_compatible_base_url:
        providers["openai_compatible"] = OpenAICompatibleProvider(
            api_key=settings.openai_compatible_api_key,
            base_url=settings.openai_compatible_base_url,
        )
    if not providers:
        providers["mock"] = MockProvider()
    return providers


class ProviderRegistry:
    def __init__(self, providers: dict[str, object] | None = None) -> None:
        self._providers = providers or {}

    def get(self, name: str):
        if name not in self._providers:
            raise KeyError(f"Unknown provider: {name}. Available: {sorted(self._providers)}")
        return self._providers[name]

    def names(self) -> list[str]:
        return sorted(self._providers)

    async def aclose(self) -> None:
        for p in self._providers.values():
            close = getattr(p, "aclose", None)
            if close:
                await close()
