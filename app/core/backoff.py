"""Retry helpers with exponential backoff and full jitter."""

from __future__ import annotations

import random


def exponential_backoff_seconds(
    attempt: int,
    *,
    base: float = 1.0,
    cap: float = 8.0,
    jitter: bool = True,
) -> float:
    """Full jitter exponential backoff.

    attempt: 0-based retry index.
    sleep = random.uniform(0, min(cap, base * 2**attempt)) when jitter=True
    """
    exp = min(cap, base * (2 ** max(attempt, 0)))
    if not jitter:
        return float(exp)
    return random.uniform(0.0, exp)


def webhook_backoff_seconds(attempt: int, *, jitter: bool = True) -> float:
    """Webhook delivery backoff: exponential up to 5 minutes with full jitter.

    attempt: 1-based delivery attempt count (matches stored webhook_attempts).
    """
    exp = min(2 ** max(attempt - 1, 0), 300)
    if not jitter:
        return float(exp)
    return random.uniform(0.0, float(exp))
