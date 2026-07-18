from __future__ import annotations

from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


class BatchCreateRequest(BaseModel):
    prompts: list[str | dict[str, Any]] | None = Field(default=None, min_length=1)
    prompts_url: str | None = None
    prompts_key: str | None = None
    provider: str | None = Field(default=None, min_length=1, max_length=64)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    cost_preference: str | None = Field(default=None, max_length=32)
    webhook_url: str | None = None
    webhook_secret: str | None = None
    chunk_size: int | None = Field(default=None, ge=1, le=10_000)
    rate_limit_rps: float | None = Field(default=None, gt=0, le=10_000)
    max_concurrency: int | None = Field(default=None, ge=1, le=1024)

    @field_validator("prompts")
    @classmethod
    def limit_inline_prompts(cls, v: list | None) -> list | None:
        if v is not None and len(v) > 50_000:
            raise ValueError(
                "Inline prompts limited to 50_000; upload NDJSON via "
                "POST /v1/batches/upload or provide prompts_url / prompts_key."
            )
        return v

    @model_validator(mode="after")
    def require_input_source(self) -> Self:
        if not self.prompts and not self.prompts_url and not self.prompts_key:
            raise ValueError("at least one of prompts, prompts_url, or prompts_key is required")
        return self


class BatchProgress(BaseModel):
    total_items: int
    completed_items: int
    failed_items: int
    retry_count: int = 0
    fraction: float


class BatchResponse(BaseModel):
    id: str
    status: str
    provider: str
    model: str
    progress: BatchProgress
    chunk_size: int
    rate_limit_rps: float
    max_concurrency: int
    prompts_key: str
    results_key: str | None = None
    manifest_key: str | None = None
    result_url: str | None = None
    webhook_url: str | None = None
    webhook_status: str
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class BatchCreateResponse(BaseModel):
    id: str
    status: str
    total_items: int
    chunk_size: int


class BatchListResponse(BaseModel):
    items: list[BatchResponse]
    limit: int
    offset: int


class WebhookTestRequest(BaseModel):
    url: HttpUrl
    secret: str | None = None


class WebhookTestResponse(BaseModel):
    ok: bool
    error: str | None = None


class HealthResponse(BaseModel):
    status: str
    version: str
    git_sha: str = "unknown"
    build_id: str | None = None
    built_at: str = "unknown"
