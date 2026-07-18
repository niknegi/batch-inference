"""Unit tests for Spaces presign public-endpoint selection."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.core.spaces import SpacesClient


@pytest.mark.asyncio
async def test_generate_presigned_url_uses_public_endpoint_when_set():
    settings = Settings(
        SPACES_ENDPOINT_URL="http://minio:9000",
        SPACES_PUBLIC_ENDPOINT_URL="http://167.71.233.238:9000",
        SPACES_ACCESS_KEY="k",
        SPACES_SECRET_KEY="s",
        SPACES_BUCKET="batch-inference",
        SPACES_FORCE_PATH_STYLE=True,
    )
    client = SpacesClient(settings)
    mock_s3 = AsyncMock()
    mock_s3.generate_presigned_url = AsyncMock(
        return_value="http://167.71.233.238:9000/batch-inference/batches/x/results.ndjson?X-Amz-Signature=abc"
    )
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_s3)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with patch.object(client._session, "client", return_value=mock_cm) as session_client:
        url = await client.generate_presigned_url("batches/x/results.ndjson")

    assert "167.71.233.238:9000" in url
    assert "minio:9000" not in url
    kwargs = session_client.call_args.kwargs
    assert kwargs["endpoint_url"] == "http://167.71.233.238:9000"


@pytest.mark.asyncio
async def test_generate_presigned_url_falls_back_to_internal_endpoint():
    settings = Settings(
        SPACES_ENDPOINT_URL="http://minio:9000",
        SPACES_ACCESS_KEY="k",
        SPACES_SECRET_KEY="s",
        SPACES_BUCKET="batch-inference",
        SPACES_FORCE_PATH_STYLE=True,
    )
    client = SpacesClient(settings)
    mock_s3 = AsyncMock()
    mock_s3.generate_presigned_url = AsyncMock(
        return_value="http://minio:9000/batch-inference/batches/x/results.ndjson?X-Amz-Signature=abc"
    )
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_s3)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with patch.object(client._session, "client", return_value=mock_cm) as session_client:
        url = await client.generate_presigned_url("batches/x/results.ndjson")

    assert "minio:9000" in url
    kwargs = session_client.call_args.kwargs
    assert kwargs["endpoint_url"] == "http://minio:9000"
