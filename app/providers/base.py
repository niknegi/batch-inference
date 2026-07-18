from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class ErrorKind(StrEnum):
    retryable = "retryable"
    terminal = "terminal"


@dataclass
class InferenceRequest:
    prompt: str
    model: str
    provider: str
    index: int
    metadata: dict[str, Any] = field(default_factory=dict)
    system: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


@dataclass
class InferenceResult:
    index: int
    ok: bool
    output: str | None = None
    error: str | None = None
    error_kind: ErrorKind | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class InferenceProvider(Protocol):
    name: str

    async def infer(self, request: InferenceRequest) -> InferenceResult: ...

    def rate_limit_key(self, request: InferenceRequest) -> str: ...

    async def aclose(self) -> None: ...
