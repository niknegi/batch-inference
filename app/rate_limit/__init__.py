from __future__ import annotations

import asyncio
import time

from redis.asyncio import Redis

from app.core.logging import get_logger
from app.core.metrics import RATE_LIMIT_WAITS

logger = get_logger(__name__)

# Lua token bucket: KEYS[1]=bucket, ARGV=[capacity, refill_rate_per_sec, tokens, now]
# Returns: 1 if acquired, 0 if not; also returns wait_ms as second value conceptually via refill.
TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local requested = tonumber(ARGV[3])
local now = tonumber(ARGV[4])
local pause_until = tonumber(redis.call('HGET', key, 'pause_until') or '0')
if pause_until > now then
  return {0, math.ceil((pause_until - now) * 1000)}
end
local data = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil then
  tokens = capacity
  ts = now
end
local elapsed = math.max(0, now - ts)
tokens = math.min(capacity, tokens + elapsed * rate)
if tokens < requested then
  local need = requested - tokens
  local wait = need / rate
  redis.call('HMSET', key, 'tokens', tokens, 'ts', now)
  redis.call('EXPIRE', key, 3600)
  return {0, math.ceil(wait * 1000)}
end
tokens = tokens - requested
redis.call('HMSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, 3600)
return {1, 0}
"""

PAUSE_LUA = """
local key = KEYS[1]
local until_ts = tonumber(ARGV[1])
redis.call('HSET', key, 'pause_until', until_ts)
redis.call('EXPIRE', key, 3600)
return 1
"""


class TokenBucketRateLimiter:
    """Shared Redis token bucket for cross-worker upstream rate limiting."""

    def __init__(self, redis: Redis, prefix: str = "rl") -> None:
        self.redis = redis
        self.prefix = prefix
        self._acquire_script = self.redis.register_script(TOKEN_BUCKET_LUA)
        self._pause_script = self.redis.register_script(PAUSE_LUA)

    def _key(self, bucket_key: str) -> str:
        return f"{self.prefix}:{bucket_key}"

    async def acquire(
        self,
        bucket_key: str,
        *,
        rate: float,
        capacity: float | None = None,
        tokens: float = 1.0,
        max_wait: float = 120.0,
    ) -> None:
        if rate <= 0:
            return
        cap = capacity if capacity is not None else max(rate, tokens)
        key = self._key(bucket_key)
        deadline = time.monotonic() + max_wait
        while True:
            now = time.time()
            result = await self._acquire_script(
                keys=[key],
                args=[cap, rate, tokens, now],
            )
            ok, wait_ms = int(result[0]), int(result[1])
            if ok == 1:
                return
            RATE_LIMIT_WAITS.labels(key=bucket_key).inc()
            wait_s = max(wait_ms / 1000.0, 0.01)
            if time.monotonic() + wait_s > deadline:
                raise TimeoutError(f"Rate limit wait exceeded for {bucket_key}")
            await asyncio.sleep(min(wait_s, 1.0))

    async def pause(self, bucket_key: str, seconds: float) -> None:
        until_ts = time.time() + max(seconds, 0.0)
        await self._pause_script(keys=[self._key(bucket_key)], args=[until_ts])
        logger.info("rate_limit_paused", key=bucket_key, seconds=seconds)


class BatchConcurrencyGate:
    """Per-batch asyncio semaphore (local to worker process)."""

    def __init__(self) -> None:
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    def get(self, batch_id: str, max_concurrency: int) -> asyncio.Semaphore:
        if batch_id not in self._semaphores:
            self._semaphores[batch_id] = asyncio.Semaphore(max(1, max_concurrency))
        return self._semaphores[batch_id]

    def drop(self, batch_id: str) -> None:
        self._semaphores.pop(batch_id, None)
