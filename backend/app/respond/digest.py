"""Opt-in weekly digest: the false and misleading claims people forwarded most this week.

Bot users send /digest to subscribe (and again to stop). Subscribing is the consent to keep
their chat address (`UserPrefs.digest_target`); it is deleted when they unsubscribe.

What is counted: claims the panel called False or Misleading with a cited source, that are not
flagged for review and contain no personal data (the same rule as the verdict cache). Only the
English claim, the verdict and the top source link are kept, per ISO week, for TREND_TTL_S.
"""

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

from pydantic import BaseModel
from redis.asyncio import Redis

from app.core.logging import get_logger
from app.core.models import CheckReport, Verdict
from app.core.prefs import PrefsStore
from app.ingestion.base import Messenger
from app.matching.verdict_cache import cacheable, normalize
from app.respond.translate import Translator

log = get_logger(__name__)

TREND_TTL_S = 15 * 24 * 3600
TOP_N = 5
DIGEST_VERDICTS = (Verdict.FALSE, Verdict.MISLEADING)
MIN_FORWARDS = 2  # a claim checked only once is not a "most forwarded" claim

ICON = {Verdict.FALSE: "🔴 False", Verdict.MISLEADING: "🟠 Misleading"}
HEADER = "🗞 TrueFalse weekly: the false claims people forwarded most this week"
EMPTY = "🗞 TrueFalse weekly: no false claim was forwarded often enough this week to list."
FOOTER = "Forward me anything you're unsure about. Send /digest to stop these messages."


class TrendEntry(BaseModel):
    claim: str  # English
    verdict: Verdict
    source: str | None = None
    count: int = 0


def week_key(when: datetime | None = None) -> str:
    year, week, _ = (when or datetime.now(UTC)).isocalendar()
    return f"{year}-W{week:02d}"


def last_week_key(now: datetime | None = None) -> str:
    return week_key((now or datetime.now(UTC)) - timedelta(days=7))


def trend_entries(report: CheckReport) -> list[TrendEntry]:
    by_id = {e.id: e for e in report.evidence}
    out = []
    for d in report.decisions:
        j = d.judgement
        if j.verdict not in DIGEST_VERDICTS or j.claim_index >= len(report.claims):
            continue
        claim = report.claims[j.claim_index]
        sources = [by_id[i] for i in j.cited_evidence_ids if i in by_id]
        if not cacheable(claim, d, sources):
            continue
        top = min(sources, key=lambda e: e.tier)
        out.append(TrendEntry(claim=claim.english, verdict=j.verdict, source=top.url))
    return out


def compose(entries: Sequence[TrendEntry]) -> str:
    """English digest text; translated per subscriber. Links are added after translation."""
    if not entries:
        return EMPTY + "\n\n" + FOOTER
    lines = [HEADER, ""]
    for n, e in enumerate(entries, 1):
        lines.append(f"{n}. {ICON[e.verdict]}: {e.claim}")
    return "\n".join([*lines, "", FOOTER])


class TrendStore(Protocol):
    async def record(self, entry: TrendEntry, week: str | None = None) -> None: ...

    async def top(self, week: str, n: int = TOP_N) -> list[TrendEntry]: ...


def _key(claim: str) -> str:
    return hashlib.sha256(normalize(claim).encode()).hexdigest()[:32]


class InMemoryTrendStore:
    def __init__(self) -> None:
        self.weeks: dict[str, dict[str, TrendEntry]] = {}

    async def record(self, entry: TrendEntry, week: str | None = None) -> None:
        bucket = self.weeks.setdefault(week or week_key(), {})
        k = _key(entry.claim)
        current = bucket.get(k) or entry.model_copy(update={"count": 0})
        bucket[k] = current.model_copy(update={"count": current.count + 1})

    async def top(self, week: str, n: int = TOP_N) -> list[TrendEntry]:
        entries = [e for e in self.weeks.get(week, {}).values() if e.count >= MIN_FORWARDS]
        return sorted(entries, key=lambda e: -e.count)[:n]


class RedisTrendStore:
    def __init__(self, redis_url: str) -> None:
        self._redis = Redis.from_url(redis_url, socket_timeout=3, socket_connect_timeout=3)

    async def record(self, entry: TrendEntry, week: str | None = None) -> None:
        week = week or week_key()
        k = _key(entry.claim)
        info = entry.model_dump_json(include={"claim", "verdict", "source"})
        async with self._redis.pipeline() as pipe:
            pipe.zincrby(f"trend:{week}", 1, k)
            pipe.hset(f"trend:{week}:info", k, info)
            pipe.expire(f"trend:{week}", TREND_TTL_S)
            pipe.expire(f"trend:{week}:info", TREND_TTL_S)
            await pipe.execute()

    async def top(self, week: str, n: int = TOP_N) -> list[TrendEntry]:
        ranked = await self._redis.zrevrangebyscore(
            f"trend:{week}", "+inf", MIN_FORWARDS, start=0, num=n, withscores=True
        )
        out = []
        for raw_key, score in ranked:
            raw = await self._redis.hget(f"trend:{week}:info", raw_key)
            if raw:
                out.append(TrendEntry(**json.loads(raw), count=int(score)))
        return out

    async def close(self) -> None:
        await self._redis.aclose()


async def send_digests(
    prefs: PrefsStore,
    trends: TrendStore,
    messenger: Messenger,
    translator: Translator | None,
    week: str | None = None,
) -> int:
    """Send last week's digest to every subscriber. Returns how many were sent."""
    entries = await trends.top(week or last_week_key())
    english = compose(entries)
    links = "\n".join(f"{n}. {e.source}" for n, e in enumerate(entries, 1) if e.source)
    translated: dict[str, str] = {}
    sent = 0
    for p in await prefs.digest_subscribers():
        if p.digest_target is None:
            continue
        lang = p.digest_language or "en"
        if lang not in translated:
            text = english
            if translator and not lang.startswith("en"):
                try:
                    text = await translator.translate(english, lang)
                except Exception as exc:
                    log.warning("digest_translate_failed", language=lang, error=str(exc))
            translated[lang] = text + (f"\n\n{links}" if links else "")
        try:
            await messenger.send_text(p.digest_target, translated[lang])
            sent += 1
        except Exception as exc:
            log.warning("digest_send_failed", error=str(exc))
    log.info("digest_sent", subscribers=sent, claims=len(entries))
    return sent
