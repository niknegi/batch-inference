"""Unit tests for exponential backoff with full jitter."""

from __future__ import annotations

from app.core.backoff import exponential_backoff_seconds, webhook_backoff_seconds


def test_exponential_backoff_jitter_in_range(monkeypatch):
    # Force deterministic midpoint-ish sample
    monkeypatch.setattr("app.core.backoff.random.uniform", lambda a, b: (a + b) / 2)

    for attempt in range(0, 6):
        delay = exponential_backoff_seconds(attempt, base=1.0, cap=8.0, jitter=True)
        cap = min(8.0, 1.0 * (2**attempt))
        assert 0.0 <= delay <= cap


def test_exponential_backoff_jitter_bounds_many_samples():
    for attempt in (0, 1, 2, 3, 10):
        cap = min(8.0, 1.0 * (2**max(attempt, 0)))
        for _ in range(50):
            delay = exponential_backoff_seconds(attempt, base=1.0, cap=8.0, jitter=True)
            assert 0.0 <= delay <= cap


def test_exponential_backoff_no_jitter_exact():
    assert exponential_backoff_seconds(0, base=1.0, cap=8.0, jitter=False) == 1.0
    assert exponential_backoff_seconds(1, base=1.0, cap=8.0, jitter=False) == 2.0
    assert exponential_backoff_seconds(2, base=1.0, cap=8.0, jitter=False) == 4.0
    assert exponential_backoff_seconds(3, base=1.0, cap=8.0, jitter=False) == 8.0
    assert exponential_backoff_seconds(10, base=1.0, cap=8.0, jitter=False) == 8.0


def test_webhook_backoff_no_jitter_exact():
    assert webhook_backoff_seconds(1, jitter=False) == 1.0
    assert webhook_backoff_seconds(2, jitter=False) == 2.0
    assert webhook_backoff_seconds(3, jitter=False) == 4.0
    assert webhook_backoff_seconds(10, jitter=False) == 300.0


def test_webhook_backoff_jitter_in_range():
    for attempt in (1, 2, 5, 10):
        cap = min(2 ** max(attempt - 1, 0), 300)
        for _ in range(40):
            delay = webhook_backoff_seconds(attempt, jitter=True)
            assert 0.0 <= delay <= float(cap)
