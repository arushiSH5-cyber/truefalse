"""Async PostgreSQL access (SQLAlchemy + asyncpg)."""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

_engine: AsyncEngine | None = None


def get_engine(database_url: str) -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(database_url, pool_pre_ping=True)
    return _engine


def get_sessionmaker(database_url: str) -> async_sessionmaker:  # type: ignore[type-arg]
    return async_sessionmaker(get_engine(database_url), expire_on_commit=False)


async def ping(database_url: str, timeout_s: float = 2.0) -> bool:
    try:
        async with asyncio.timeout(timeout_s):
            async with get_engine(database_url).connect() as conn:
                await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def dispose() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
