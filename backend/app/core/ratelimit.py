"""Fixed-window rate limiting per anonymous client (protects API costs on public endpoints)."""

import time
from typing import Protocol

from redis.asyncio import Redis


class RateLimiter(Protocol):
    async def allow(self, key: str, limit: int, window_s: int = 3600) -> bool: ...


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def allow(self, key: str, limit: int, window_s: int = 3600) -> bool:
        bucket = f"{key}:{int(time.time() // window_s)}"
        self.counts[bucket] = self.counts.get(bucket, 0) + 1
        return self.counts[bucket] <= limit


class RedisRateLimiter:
    def __init__(self, redis_url: str) -> None:
        self._redis = Redis.from_url(redis_url, socket_timeout=3, socket_connect_timeout=3)

    async def allow(self, key: str, limit: int, window_s: int = 3600) -> bool:
        bucket = f"rl:{key}:{int(time.time() // window_s)}"
        async with self._redis.pipeline() as pipe:
            pipe.incr(bucket)
            pipe.expire(bucket, window_s)
            count, _ = await pipe.execute()
        return int(count) <= limit
