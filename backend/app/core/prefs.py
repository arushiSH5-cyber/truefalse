"""Per-user preferences, keyed only by the pseudonymous user hash (never a raw id)."""

from typing import Protocol

from pydantic import BaseModel
from redis.asyncio import Redis

from app.core.models import ReplyTarget


class UserPrefs(BaseModel):
    user_hash: str
    reply_language: str | None = None  # set by sending a language name; None = auto-detect
    voice_replies: bool = False  # always add a voice note (voice-in already gets voice-out)
    welcomed: bool = False
    digest_opt_in: bool = False
    digest_language: str | None = None
    # Stored only while the user has opted in to the digest (explicit consent), else None.
    digest_target: ReplyTarget | None = None


class PrefsStore(Protocol):
    async def get(self, user_hash: str) -> UserPrefs | None: ...

    async def save(self, prefs: UserPrefs) -> None: ...

    async def digest_subscribers(self) -> list[UserPrefs]: ...


class InMemoryPrefsStore:
    def __init__(self) -> None:
        self.items: dict[str, UserPrefs] = {}

    async def get(self, user_hash: str) -> UserPrefs | None:
        p = self.items.get(user_hash)
        return p.model_copy() if p else None

    async def save(self, prefs: UserPrefs) -> None:
        self.items[prefs.user_hash] = prefs.model_copy()

    async def digest_subscribers(self) -> list[UserPrefs]:
        return [p for p in self.items.values() if p.digest_opt_in and p.digest_target]


class RedisPrefsStore:
    PREFIX = "prefs:"
    DIGEST_SET = "digest:subscribers"

    def __init__(self, redis_url: str) -> None:
        self._redis = Redis.from_url(redis_url, socket_timeout=3, socket_connect_timeout=3)

    async def get(self, user_hash: str) -> UserPrefs | None:
        raw = await self._redis.get(self.PREFIX + user_hash)
        return UserPrefs.model_validate_json(raw) if raw else None

    async def save(self, prefs: UserPrefs) -> None:
        await self._redis.set(self.PREFIX + prefs.user_hash, prefs.model_dump_json())
        if prefs.digest_opt_in and prefs.digest_target:
            await self._redis.sadd(self.DIGEST_SET, prefs.user_hash)
        else:
            await self._redis.srem(self.DIGEST_SET, prefs.user_hash)

    async def digest_subscribers(self) -> list[UserPrefs]:
        hashes = await self._redis.smembers(self.DIGEST_SET)
        out = []
        for h in hashes:
            p = await self.get(h.decode() if isinstance(h, bytes) else h)
            if p and p.digest_opt_in and p.digest_target:
                out.append(p)
        return out

    async def close(self) -> None:
        await self._redis.aclose()
