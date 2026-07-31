"""Sliding-window rate limiter. Redis-backed when configured; in-memory fallback
for dev/tests. Per-session and per-IP (§9.2)."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Protocol


class RateLimiter(Protocol):
    async def allow(self, key: str) -> bool: ...


class MemoryRateLimiter:
    def __init__(self, max_requests: int = 30, window_s: float = 60.0) -> None:
        self.max_requests = max_requests
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window_s:
            hits.popleft()
        if len(hits) >= self.max_requests:
            return False
        hits.append(now)
        return True


class RedisRateLimiter:
    def __init__(self, redis_url: str, max_requests: int = 30, window_s: int = 60) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(redis_url)
        self.max_requests = max_requests
        self.window_s = window_s

    async def allow(self, key: str) -> bool:
        bucket = f"rl:{key}"
        count = await self._redis.incr(bucket)
        if count == 1:
            await self._redis.expire(bucket, self.window_s)
        return int(count) <= self.max_requests


def build_rate_limiter(redis_url: str, max_requests: int = 30, window_s: int = 60) -> RateLimiter:
    if redis_url:
        try:
            return RedisRateLimiter(redis_url, max_requests, window_s)
        except Exception:  # pragma: no cover - redis missing/unreachable at boot
            pass
    return MemoryRateLimiter(max_requests, float(window_s))
