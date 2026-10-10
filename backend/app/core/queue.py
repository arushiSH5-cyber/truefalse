"""Redis connection + arq background-job helpers."""

import asyncio
from typing import Any

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings
from redis.asyncio import Redis


def redis_settings(redis_url: str) -> RedisSettings:
    return RedisSettings.from_dsn(redis_url)


async def ping(redis_url: str, timeout_s: float = 2.0) -> bool:
    client = Redis.from_url(redis_url, socket_connect_timeout=timeout_s, socket_timeout=timeout_s)
    try:
        async with asyncio.timeout(timeout_s):
            return bool(await client.ping())
    except Exception:
        return False
    finally:
        await client.aclose()


class ArqJobQueue:
    """JobQueue backed by arq. The pool is created on first use so the API boots without Redis."""

    def __init__(self, redis_url: str) -> None:
        self._settings = redis_settings(redis_url)
        self._pool: ArqRedis | None = None
        self._lock = asyncio.Lock()

    async def enqueue(self, function: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            if self._pool is None:
                self._pool = await create_pool(self._settings)
        await self._pool.enqueue_job(function, payload)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None
