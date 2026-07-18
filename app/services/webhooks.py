from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger
from app.core.metrics import WEBHOOK_DELIVERIES

logger = get_logger(__name__)


def sign_payload(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def build_webhook_payload(
    *,
    event: str,
    batch_id: str,
    status: str,
    result_url: str | None,
    stats: dict[str, Any],
    completed_at: datetime | None,
) -> dict[str, Any]:
    return {
        "event": event,
        "batch_id": batch_id,
        "status": status,
        "result_url": result_url,
        "stats": stats,
        "completed_at": completed_at.isoformat() if completed_at else None,
        "timestamp": datetime.now(UTC).isoformat(),
    }


async def deliver_webhook(
    *,
    url: str,
    secret: str | None,
    payload: dict[str, Any],
    timeout: float = 30.0,
) -> tuple[bool, str | None]:
    body = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "batch-inference-webhook/0.1",
        "X-Batch-Id": str(payload.get("batch_id", "")),
        "X-Webhook-Event": str(payload.get("event", "")),
    }
    if secret:
        headers["X-Webhook-Signature"] = sign_payload(secret, body)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, content=body, headers=headers)
        if 200 <= resp.status_code < 300:
            WEBHOOK_DELIVERIES.labels(status="success").inc()
            return True, None
        WEBHOOK_DELIVERIES.labels(status="http_error").inc()
        return False, f"HTTP {resp.status_code}: {resp.text[:300]}"
    except Exception as exc:  # noqa: BLE001
        WEBHOOK_DELIVERIES.labels(status="transport_error").inc()
        logger.warning("webhook_delivery_failed", url=url, error=str(exc))
        return False, str(exc)


def webhook_backoff_seconds(attempt: int) -> int:
    """Exponential backoff capped at 5 minutes: 1, 2, 4, ... 300."""
    return min(2 ** max(attempt - 1, 0), 300)
