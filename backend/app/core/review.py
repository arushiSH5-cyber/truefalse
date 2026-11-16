"""Human review queue and user feedback.

Answers land here for a person to check when:
- the panel flagged them (`needs_human_review`: judges disagree, a fact-check contradicts the
  verdict, too few judges answered), or
- a user said the answer was wrong (thumbs down / "Report a mistake" in the web app and the
  extension, `/wrong` in the bots).

What is stored: the claims (PII-redacted), the verdict, the cited source links and the reason.
Never user ids, chat ids, raw messages or media. Items expire after REVIEW_TTL_S. A reviewer
lists open items and records the right verdict (`/api/review`, protected by REVIEW_TOKEN);
resolved items can be exported as eval data (`own_forwards` format).

To answer `/wrong` in the bots, the last answer per pseudonymous user is kept for
RECENT_TTL_S (same redacted fields), and only the user who got it can report it.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.core.models import CheckReport, Verdict
from app.safety.pii_redact import redact

REVIEW_TTL_S = 30 * 24 * 3600
RECENT_TTL_S = 24 * 3600
MAX_NOTE = 500
MAX_CLAIM = 500

Kind = Literal["auto_flag", "user_report"]


class ReviewItem(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    created: datetime = Field(default_factory=lambda: datetime.now(UTC))
    kind: Kind = "auto_flag"
    platform: str = "web"
    language: str = "und"
    claims: list[str] = Field(default_factory=list)
    verdict: Verdict = Verdict.UNVERIFIED
    confidence: str = "low"
    reasons: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)  # cited URLs
    note: str | None = None  # what the user said was wrong (redacted)
    status: Literal["open", "resolved"] = "open"
    correct_verdict: Verdict | None = None
    reviewer_note: str | None = None


def clean_note(note: str | None) -> str | None:
    if not note or not note.strip():
        return None
    return redact(note.strip())[:MAX_NOTE]


def item_from_report(report: CheckReport, platform: str, kind: Kind = "auto_flag") -> ReviewItem:
    by_id = {e.id: e for e in report.evidence}
    cited = dict.fromkeys(i for j in report.judgements for i in j.cited_evidence_ids)
    return ReviewItem(
        kind=kind,
        platform=platform,
        language=report.language,
        claims=[redact(c.original)[:MAX_CLAIM] for c in report.claims],
        verdict=report.overall,
        confidence=report.overall_confidence.value,
        reasons=list(report.review_reasons),
        sources=[by_id[i].url for i in cited if i in by_id],
    )


def item_from_public(
    *,
    platform: str,
    language: str,
    claims: Sequence[str],
    verdict: str,
    confidence: str,
    sources: Sequence[str],
    reasons: Sequence[str] = (),
) -> ReviewItem:
    """From the web job's public result (already free of user data)."""
    return ReviewItem(
        kind="user_report",
        platform=platform,
        language=language,
        claims=[redact(c)[:MAX_CLAIM] for c in claims],
        verdict=Verdict(verdict),
        confidence=confidence,
        reasons=list(reasons),
        sources=list(sources),
    )


def as_eval_row(item: ReviewItem) -> dict[str, object] | None:
    """A resolved item in `eval/datasets/own_forwards` format (one row per single-claim item)."""
    if item.status != "resolved" or item.correct_verdict is None or len(item.claims) != 1:
        return None
    return {
        "id": f"review-{item.id}",
        "language": item.language,
        "text": item.claims[0],
        "expected": item.correct_verdict.value,
        "source": item.sources[0] if item.sources else None,
    }


class FeedbackStats(BaseModel):
    up: int = 0
    down: int = 0
    open_reviews: int = 0


class ReviewStore(Protocol):
    async def add(self, item: ReviewItem) -> ReviewItem: ...

    async def get(self, item_id: str) -> ReviewItem | None: ...

    async def save(self, item: ReviewItem) -> None: ...

    async def list(self, status: str = "open", limit: int = 50) -> list[ReviewItem]: ...

    async def count_feedback(self, rating: Literal["up", "down"]) -> None: ...

    async def stats(self) -> FeedbackStats: ...

    async def remember_recent(self, user_hash: str, item: ReviewItem) -> None: ...

    async def take_recent(self, user_hash: str) -> ReviewItem | None: ...


class InMemoryReviewStore:
    def __init__(self) -> None:
        self.items: dict[str, ReviewItem] = {}
        self.recent: dict[str, ReviewItem] = {}
        self.counts = {"up": 0, "down": 0}

    async def add(self, item: ReviewItem) -> ReviewItem:
        self.items[item.id] = item.model_copy(deep=True)
        return item

    async def get(self, item_id: str) -> ReviewItem | None:
        item = self.items.get(item_id)
        return item.model_copy(deep=True) if item else None

    async def save(self, item: ReviewItem) -> None:
        self.items[item.id] = item.model_copy(deep=True)

    async def list(self, status: str = "open", limit: int = 50) -> list[ReviewItem]:
        found = [i for i in self.items.values() if status == "all" or i.status == status]
        return sorted(found, key=lambda i: i.created, reverse=True)[:limit]

    async def count_feedback(self, rating: Literal["up", "down"]) -> None:
        self.counts[rating] += 1

    async def stats(self) -> FeedbackStats:
        n_open = sum(1 for i in self.items.values() if i.status == "open")
        return FeedbackStats(up=self.counts["up"], down=self.counts["down"], open_reviews=n_open)

    async def remember_recent(self, user_hash: str, item: ReviewItem) -> None:
        self.recent[user_hash] = item.model_copy(deep=True)

    async def take_recent(self, user_hash: str) -> ReviewItem | None:
        return self.recent.pop(user_hash, None)


class RedisReviewStore:
    INDEX = "review:index"  # sorted set: id -> created timestamp

    def __init__(self, redis_url: str) -> None:
        self._redis = Redis.from_url(redis_url, socket_timeout=3, socket_connect_timeout=3)

    async def add(self, item: ReviewItem) -> ReviewItem:
        await self.save(item)
        await self._redis.zadd(self.INDEX, {item.id: item.created.timestamp()})
        return item

    async def get(self, item_id: str) -> ReviewItem | None:
        raw = await self._redis.get(f"review:{item_id}")
        return ReviewItem.model_validate_json(raw) if raw else None

    async def save(self, item: ReviewItem) -> None:
        await self._redis.set(f"review:{item.id}", item.model_dump_json(), ex=REVIEW_TTL_S)

    async def list(self, status: str = "open", limit: int = 50) -> list[ReviewItem]:
        cutoff = datetime.now(UTC).timestamp() - REVIEW_TTL_S
        await self._redis.zremrangebyscore(self.INDEX, "-inf", cutoff)
        ids = await self._redis.zrevrange(self.INDEX, 0, 999)
        out: list[ReviewItem] = []
        for raw_id in ids:
            item = await self.get(raw_id.decode() if isinstance(raw_id, bytes) else raw_id)
            if item and (status == "all" or item.status == status):
                out.append(item)
                if len(out) >= limit:
                    break
        return out

    async def count_feedback(self, rating: Literal["up", "down"]) -> None:
        await self._redis.hincrby("feedback:counts", rating, 1)

    async def stats(self) -> FeedbackStats:
        counts = await self._redis.hgetall("feedback:counts")
        c = {(k.decode() if isinstance(k, bytes) else k): int(v) for k, v in counts.items()}
        return FeedbackStats(
            up=c.get("up", 0), down=c.get("down", 0), open_reviews=len(await self.list("open"))
        )

    async def remember_recent(self, user_hash: str, item: ReviewItem) -> None:
        await self._redis.set(f"recent:{user_hash}", item.model_dump_json(), ex=RECENT_TTL_S)

    async def take_recent(self, user_hash: str) -> ReviewItem | None:
        raw = await self._redis.getdel(f"recent:{user_hash}")
        return ReviewItem.model_validate_json(raw) if raw else None

    async def close(self) -> None:
        await self._redis.aclose()
