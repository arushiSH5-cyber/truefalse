"""Redis connection + arq background-job helpers."""

import asyncio

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
