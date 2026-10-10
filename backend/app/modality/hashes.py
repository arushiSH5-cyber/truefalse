"""'Seen before' detection for images, by perceptual hash.

Only the 64-bit hash is stored, never the image. A near-identical image (small Hamming
distance) that was checked earlier is reported, with the verdict it received then.
"""

from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.db_models import ImageHash
from app.core.models import Verdict
from app.modality.media import hamming

MAX_DISTANCE = 8  # out of 64 bits


class SeenImage(BaseModel):
    phash: str
    times_seen: int
    first_seen: datetime
    last_verdict: Verdict | None


class ImageHashStore(Protocol):
    async def lookup(self, phash: str) -> SeenImage | None: ...

    async def record(self, phash: str, verdict: Verdict | None) -> None: ...


class InMemoryImageHashStore:
    def __init__(self) -> None:
        self._items: dict[str, SeenImage] = {}

    def _near(self, phash: str) -> SeenImage | None:
        best = min(self._items.values(), key=lambda s: hamming(s.phash, phash), default=None)
        return best if best and hamming(best.phash, phash) <= MAX_DISTANCE else None

    async def lookup(self, phash: str) -> SeenImage | None:
        return self._near(phash)

    async def record(self, phash: str, verdict: Verdict | None) -> None:
        seen = self._near(phash)
        if seen:
            seen.times_seen += 1
            seen.last_verdict = verdict or seen.last_verdict
        else:
            self._items[phash] = SeenImage(
                phash=phash, times_seen=1, first_seen=datetime.now(UTC), last_verdict=verdict
            )


class PgImageHashStore:
    """Hashes are compared in Python over a bounded recent window; fine at research scale."""

    def __init__(self, engine: AsyncEngine, window: int = 50_000) -> None:
        self._engine = engine
        self._window = window

    async def _near(self, session: AsyncSession, phash: str) -> ImageHash | None:
        rows = (
            await session.execute(
                select(ImageHash).order_by(ImageHash.last_seen.desc()).limit(self._window)
            )
        ).scalars()
        best = min(rows, key=lambda r: hamming(r.phash, phash), default=None)
        return best if best and hamming(best.phash, phash) <= MAX_DISTANCE else None

    async def lookup(self, phash: str) -> SeenImage | None:
        async with AsyncSession(self._engine) as session:
            row = await self._near(session, phash)
            if row is None:
                return None
            return SeenImage(
                phash=row.phash,
                times_seen=row.times_seen,
                first_seen=row.first_seen,
                last_verdict=Verdict(row.last_verdict) if row.last_verdict else None,
            )

    async def record(self, phash: str, verdict: Verdict | None) -> None:
        async with AsyncSession(self._engine) as session, session.begin():
            row = await self._near(session, phash)
            now = datetime.now(UTC)
            if row is None:
                session.add(
                    ImageHash(
                        phash=phash,
                        times_seen=1,
                        first_seen=now,
                        last_seen=now,
                        last_verdict=verdict.value if verdict else None,
                    )
                )
            else:
                row.times_seen += 1
                row.last_seen = now
                if verdict:
                    row.last_verdict = verdict.value
