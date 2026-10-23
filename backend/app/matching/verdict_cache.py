"""Cache of finished verdicts, so the same viral forward is not researched and judged again.

What is stored, per claim: the claim text (as extracted, original language and English), the
decision, and the public sources it cites, plus a timestamp. Never user ids, chat ids, or the
message the claim was found in. Entries expire (default 7 days) because facts change.

Only confident, evidence-backed decisions are cached. Unverified verdicts, decisions flagged for
human review, and claims that look like they contain personal data (phone numbers, emails,
handles) are not, so a claim about a private person in a private chat is never kept.

Keys are hashes of the normalized original text and of the normalized English translation;
either one matching is a hit (the English translation varies a little between runs).
"""

import hashlib
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.core.logging import get_logger
from app.core.models import Claim, ClaimDecision, Evidence, Verdict
from app.safety.pii_redact import redact

log = get_logger(__name__)

DEFAULT_TTL_S = 7 * 24 * 3600
MIN_KEY_CHARS = 12  # shorter claims ("it's fake") are too generic to reuse a verdict for
_SPACE = re.compile(r"\s+")


class CachedVerdict(BaseModel):
    claim: str  # English
    original: str
    decision: ClaimDecision
    sources: list[Evidence]  # the cited public sources only
    cached_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def normalize(text: str) -> str:
    """Case, width, punctuation, emoji and spacing differences do not change the key."""
    text = unicodedata.normalize("NFKC", text).casefold()
    kept = "".join(" " if unicodedata.category(c)[0] in "PSZC" else c for c in text)
    return _SPACE.sub(" ", kept).strip()


def cache_keys(claim: Claim) -> list[str]:
    keys = []
    for prefix, text in (("o", claim.original), ("e", claim.english)):
        norm = normalize(text)
        if len(norm) >= MIN_KEY_CHARS:
            keys.append(prefix + ":" + hashlib.sha256(norm.encode()).hexdigest()[:40])
    return keys


def cacheable(claim: Claim, decision: ClaimDecision, sources: Sequence[Evidence]) -> bool:
    j = decision.judgement
    return (
        j.verdict is not Verdict.UNVERIFIED
        and not decision.needs_human_review
        and bool(sources)
        and redact(claim.original) == claim.original
        and redact(claim.english) == claim.english
    )


def entry_for(
    claim: Claim, decision: ClaimDecision, evidence: Mapping[str, Evidence]
) -> CachedVerdict | None:
    sources = [evidence[i] for i in decision.judgement.cited_evidence_ids if i in evidence]
    if not cacheable(claim, decision, sources):
        return None
    return CachedVerdict(
        claim=claim.english, original=claim.original, decision=decision, sources=sources
    )


def reuse(entry: CachedVerdict, claim_index: int) -> tuple[ClaimDecision, list[Evidence]]:
    """The cached decision for claim `claim_index` of this check, with fresh evidence ids."""
    ids = {e.id: f"C{claim_index}-{n}" for n, e in enumerate(entry.sources, 1)}
    sources = [e.model_copy(update={"id": ids[e.id]}) for e in entry.sources]
    judgement = entry.decision.judgement.model_copy(
        update={
            "claim_index": claim_index,
            "cited_evidence_ids": [ids[i] for i in entry.decision.judgement.cited_evidence_ids],
        }
    )
    return entry.decision.model_copy(update={"judgement": judgement}), sources


class VerdictCache(Protocol):
    async def get(self, claim: Claim) -> CachedVerdict | None: ...

    async def put(self, claim: Claim, entry: CachedVerdict) -> None: ...


class InMemoryVerdictCache:
    def __init__(self) -> None:
        self.items: dict[str, str] = {}

    async def get(self, claim: Claim) -> CachedVerdict | None:
        for key in cache_keys(claim):
            if key in self.items:
                return CachedVerdict.model_validate_json(self.items[key])
        return None

    async def put(self, claim: Claim, entry: CachedVerdict) -> None:
        for key in cache_keys(claim):
            self.items[key] = entry.model_dump_json()


class RedisVerdictCache:
    PREFIX = "verdict:"

    def __init__(self, redis_url: str, ttl_s: int = DEFAULT_TTL_S) -> None:
        self._redis = Redis.from_url(redis_url, socket_timeout=3, socket_connect_timeout=3)
        self._ttl = ttl_s

    async def get(self, claim: Claim) -> CachedVerdict | None:
        keys = cache_keys(claim)
        if not keys:
            return None
        for raw in await self._redis.mget([self.PREFIX + k for k in keys]):
            if raw:
                return CachedVerdict.model_validate_json(raw)
        return None

    async def put(self, claim: Claim, entry: CachedVerdict) -> None:
        data = entry.model_dump_json()
        async with self._redis.pipeline() as pipe:
            for key in cache_keys(claim):
                pipe.set(self.PREFIX + key, data, ex=self._ttl)
            await pipe.execute()

    async def close(self) -> None:
        await self._redis.aclose()
