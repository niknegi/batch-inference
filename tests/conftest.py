"""Shared fixtures for unit and integration tests (no Docker / real network)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base


class FakeSpaces:
    """In-memory Spaces stand-in matching the SpacesClient surface used by services/workers."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def upload_prompts_ndjson(self, key: str, prompts) -> int:
        lines = []
        for i, p in enumerate(prompts):
            if isinstance(p, str):
                obj = {"index": i, "prompt": p}
            else:
                obj = {"index": i, **p}
            lines.append(json.dumps(obj).encode())
        self.objects[key] = b"\n".join(lines) + b"\n"
        return len(prompts)

    async def upload_raw_ndjson(self, key: str, data: bytes) -> int:
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
                    out.append(json.dumps(parsed).encode())
                else:
                    out.append(json.dumps({"index": idx, "prompt": str(parsed)}).encode())
            except json.JSONDecodeError:
                out.append(json.dumps({"index": idx, "prompt": text}).encode())
            idx += 1
        self.objects[key] = b"\n".join(out) + (b"\n" if out else b"")
        return len(out)

    async def count_ndjson_lines(self, key: str) -> int:
        data = self.objects.get(key, b"").decode().strip()
        if not data:
            return 0
        return len([ln for ln in data.split("\n") if ln.strip()])

    async def copy_key(self, src_key: str, dest_key: str) -> str:
        if src_key not in self.objects:
            raise KeyError(src_key)
        self.objects[dest_key] = self.objects[src_key]
        return dest_key

    async def download_url_to_key(self, url: str, key: str) -> int:
        # Tests should monkeypatch httpx if they need real fetch; default stores URL as prompt.
        return await self.upload_raw_ndjson(key, f'{{"prompt":"{url}"}}\n'.encode())

    async def read_line_range(self, key: str, offset: int, limit: int) -> list[dict]:
        data = self.objects[key].decode().strip().split("\n")
        rows = [json.loads(line) for line in data if line]
        return rows[offset : offset + limit]

    async def write_chunk_results(self, key: str, rows) -> str:
        body = b"\n".join(json.dumps(r).encode() for r in rows) + b"\n"
        self.objects[key] = body
        return key

    async def concatenate_chunks(self, chunk_keys, dest_key: str) -> str:
        parts = [self.objects[k] for k in chunk_keys]
        self.objects[dest_key] = b"".join(parts)
        return dest_key

    async def put_json(self, key: str, payload: Any) -> str:
        self.objects[key] = json.dumps(payload).encode()
        return key

    async def generate_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return f"https://spaces.example/{key}?exp={expires_in}"


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def spaces() -> FakeSpaces:
    return FakeSpaces()
