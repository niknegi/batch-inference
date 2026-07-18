from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from typing import Any

import aioboto3
from botocore.client import Config

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class SpacesClient:
    """S3-compatible client for DigitalOcean Spaces / MinIO."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._session = aioboto3.Session()

    def _client_kwargs(self) -> dict[str, Any]:
        return {
            "service_name": "s3",
            "endpoint_url": self.settings.spaces_endpoint_url,
            "aws_access_key_id": self.settings.spaces_access_key,
            "aws_secret_access_key": self.settings.spaces_secret_key,
            "region_name": self.settings.spaces_region,
            "config": Config(
                s3={"addressing_style": "path" if self.settings.spaces_force_path_style else "auto"}
            ),
        }

    @asynccontextmanager
    async def _client(self):
        async with self._session.client(**self._client_kwargs()) as client:
            yield client

    @property
    def bucket(self) -> str:
        return self.settings.spaces_bucket

    async def ensure_bucket(self) -> None:
        async with self._client() as client:
            try:
                await client.head_bucket(Bucket=self.bucket)
            except Exception:
                await client.create_bucket(Bucket=self.bucket)
                logger.info("created_bucket", bucket=self.bucket)

    async def put_bytes(
        self, key: str, data: bytes, content_type: str = "application/x-ndjson"
    ) -> str:
        async with self._client() as client:
            await client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        return key

    async def put_json(self, key: str, payload: Any) -> str:
        body = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
        return await self.put_bytes(key, body, content_type="application/json")

    async def upload_prompts_ndjson(self, key: str, prompts: Sequence[str | dict[str, Any]]) -> int:
        lines: list[bytes] = []
        for i, prompt in enumerate(prompts):
            if isinstance(prompt, str):
                obj = {"index": i, "prompt": prompt}
            else:
                obj = {"index": i, **prompt}
                obj.setdefault("index", i)
            lines.append(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
        body = b"\n".join(lines) + (b"\n" if lines else b"")
        await self.put_bytes(key, body)
        return len(prompts)

    async def stream_lines(self, key: str) -> AsyncIterator[str]:
        async with self._client() as client:
            resp = await client.get_object(Bucket=self.bucket, Key=key)
            body = resp["Body"]
            buffer = b""
            while True:
                chunk = await body.read(64 * 1024)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if line.strip():
                        yield line.decode("utf-8")
            if buffer.strip():
                yield buffer.decode("utf-8")

    async def read_line_range(self, key: str, offset: int, limit: int) -> list[dict[str, Any]]:
        """Read NDJSON lines in [offset, offset+limit) by streaming (memory-safe for large files)."""
        results: list[dict[str, Any]] = []
        end = offset + limit
        idx = 0
        async for line in self.stream_lines(key):
            if idx >= end:
                break
            if idx >= offset:
                results.append(json.loads(line))
            idx += 1
        return results

    async def write_chunk_results(self, key: str, rows: Iterable[dict[str, Any]]) -> str:
        lines = [json.dumps(row, ensure_ascii=False, default=str).encode("utf-8") for row in rows]
        body = b"\n".join(lines) + (b"\n" if lines else b"")
        return await self.put_bytes(key, body)

    async def concatenate_chunks(self, chunk_keys: Sequence[str], dest_key: str) -> str:
        """Concatenate chunk NDJSON objects into a single results object."""
        parts: list[bytes] = []
        async with self._client() as client:
            for key in chunk_keys:
                resp = await client.get_object(Bucket=self.bucket, Key=key)
                data = await resp["Body"].read()
                if data and not data.endswith(b"\n"):
                    data += b"\n"
                parts.append(data)
            body = b"".join(parts)
            await client.put_object(
                Bucket=self.bucket,
                Key=dest_key,
                Body=body,
                ContentType="application/x-ndjson",
            )
        return dest_key

    async def generate_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        async with self._client() as client:
            return await client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_in,
            )

    async def get_bytes(self, key: str) -> bytes:
        async with self._client() as client:
            resp = await client.get_object(Bucket=self.bucket, Key=key)
            return await resp["Body"].read()


def batch_prefix(batch_id: str) -> str:
    return f"batches/{batch_id}"


def prompts_key(batch_id: str) -> str:
    return f"{batch_prefix(batch_id)}/prompts.ndjson"


def chunk_result_key(batch_id: str, chunk_index: int) -> str:
    return f"{batch_prefix(batch_id)}/chunks/{chunk_index:06d}.ndjson"


def results_key(batch_id: str) -> str:
    return f"{batch_prefix(batch_id)}/results.ndjson"


def manifest_key(batch_id: str) -> str:
    return f"{batch_prefix(batch_id)}/manifest.json"
