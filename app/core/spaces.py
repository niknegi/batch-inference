from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from typing import Any

import aioboto3
import httpx
from botocore.client import Config

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class SpacesClient:
    """S3-compatible client for DigitalOcean Spaces / MinIO."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._session = aioboto3.Session()

    def _client_kwargs(self, *, endpoint_url: str | None = None) -> dict[str, Any]:
        return {
            "service_name": "s3",
            "endpoint_url": endpoint_url or self.settings.spaces_endpoint_url,
            "aws_access_key_id": self.settings.spaces_access_key,
            "aws_secret_access_key": self.settings.spaces_secret_key,
            "region_name": self.settings.spaces_region,
            "config": Config(
                s3={"addressing_style": "path" if self.settings.spaces_force_path_style else "auto"}
            ),
        }

    @asynccontextmanager
    async def _client(self, *, endpoint_url: str | None = None):
        async with self._session.client(**self._client_kwargs(endpoint_url=endpoint_url)) as client:
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

    async def count_ndjson_lines(self, key: str) -> int:
        count = 0
        async for _ in self.stream_lines(key):
            count += 1
        return count

    async def upload_raw_ndjson(self, key: str, data: bytes) -> int:
        """Upload NDJSON (or plain-text lines) and return non-empty line count.

        Valid JSON objects are preserved (``index`` set if missing). Plain text
        lines are normalized to ``{"index": i, "prompt": "..."}``.
        """
        out: list[bytes] = []
        idx = 0
        for raw in data.splitlines():
            line = raw.strip()
            if not line:
                continue
            text = line.decode("utf-8")
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    parsed.setdefault("index", idx)
                    out.append(json.dumps(parsed, ensure_ascii=False).encode("utf-8"))
                else:
                    out.append(
                        json.dumps(
                            {"index": idx, "prompt": str(parsed)}, ensure_ascii=False
                        ).encode("utf-8")
                    )
            except json.JSONDecodeError:
                out.append(
                    json.dumps({"index": idx, "prompt": text}, ensure_ascii=False).encode(
                        "utf-8"
                    )
                )
            idx += 1
        body = b"\n".join(out) + (b"\n" if out else b"")
        await self.put_bytes(key, body)
        return len(out)

    async def copy_key(self, src_key: str, dest_key: str) -> str:
        """Copy an object within the configured bucket."""
        async with self._client() as client:
            await client.copy_object(
                Bucket=self.bucket,
                CopySource={"Bucket": self.bucket, "Key": src_key},
                Key=dest_key,
            )
        return dest_key

    async def download_url_to_key(self, url: str, key: str) -> int:
        """Fetch prompts from ``url`` and store as NDJSON under ``key``."""
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        return await self.upload_raw_ndjson(key, resp.content)

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
        """Sign a GET URL.

        Uses ``SPACES_PUBLIC_ENDPOINT_URL`` when set so clients outside Docker can
        open the link. Signing is local (no network call to the endpoint).
        """
        public = (self.settings.spaces_public_endpoint_url or "").strip() or None
        async with self._client(endpoint_url=public) as client:
            return await client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_in,
            )

    async def stream_object(self, key: str) -> AsyncIterator[bytes]:
        """Yield raw object bytes in chunks (keeps the S3 client open while streaming)."""
        async with self._client() as client:
            resp = await client.get_object(Bucket=self.bucket, Key=key)
            body = resp["Body"]
            while True:
                chunk = await body.read(64 * 1024)
                if not chunk:
                    break
                yield chunk

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
