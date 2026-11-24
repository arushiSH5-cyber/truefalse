"""'For you': recent fact-checks from real fact-checkers, picked to match what someone checked.

Recommendations are never written by the AI. They are articles from fact-checkers' public RSS
feeds; the AI only chooses which ones fit the person's recent checks and says why in a few words.
If the AI is unavailable, a simple word-overlap ranking is used instead.
"""

import asyncio
import html
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from app.core import prompts
from app.core.llm import StructuredLLM
from app.core.logging import get_logger
from app.myths import MYTHS, Myth

log = get_logger(__name__)

# Public feeds that work without keys. (publisher, url, language)
FEEDS: tuple[tuple[str, str, str], ...] = (
    ("Snopes", "https://www.snopes.com/feed/", "en"),
    ("Full Fact", "https://fullfact.org/feed/", "en"),
    ("FactCheck.org", "https://www.factcheck.org/feed/", "en"),
    ("Lead Stories", "https://leadstories.com/atom.xml", "en"),
    ("Alt News", "https://www.altnews.in/feed/", "en"),
    ("Factly", "https://factly.in/feed/", "en"),
    ("Vishvas News", "https://www.vishvasnews.com/feed/", "hi"),
    ("Maldita", "https://maldita.es/feed/", "es"),
)
FEED_TTL_S = 3600
MAX_ITEMS = 8
AI_BUDGET_S = 12
ATOM = "{http://www.w3.org/2005/Atom}"


class FeedItem(BaseModel):
    title: str
    url: str
    publisher: str
    language: str
    summary: str = ""
    published: float | None = None  # unix time


Verdict = Literal["false", "misleading", "true", "mixed", ""]


class Recommendation(FeedItem):
    why: str = ""
    myth: str = ""  # the claim the fact-checkers looked at, said plainly
    verdict: Verdict = ""  # what they found; "" when the article isn't a verdict on a claim
    truth: str = ""  # one line: what is actually true


class ForYou(BaseModel):
    topics: list[str]
    items: list[Recommendation]  # recent fact-checks from the feeds
    myths: list[Recommendation] = Field(default_factory=list)  # common myths, busted


class _Pick(BaseModel):
    index: int
    why: str = Field(default="", max_length=120)
    myth: str = Field(default="", max_length=200)
    verdict: Verdict = ""
    truth: str = Field(default="", max_length=240)


class _Picks(BaseModel):
    topics: list[str] = Field(default_factory=list, max_length=4)
    picks: list[_Pick] = Field(default_factory=list, max_length=MAX_ITEMS)


def _clean(text: str | None, limit: int = 280) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _time(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        pass
    try:
        from datetime import datetime

        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_feed(xml: str, publisher: str, language: str) -> list[FeedItem]:
    """RSS 2.0 items or Atom entries. Bad feeds give an empty list, never an error."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    out: list[FeedItem] = []
    for item in root.iter("item"):
        title, link = _clean(item.findtext("title"), 200), (item.findtext("link") or "").strip()
        if title and link.startswith("http"):
            out.append(
                FeedItem(
                    title=title,
                    url=link,
                    publisher=publisher,
                    language=language,
                    summary=_clean(item.findtext("description")),
                    published=_time(item.findtext("pubDate")),
                )
            )
    for entry in root.iter(f"{ATOM}entry"):
        link_el = entry.find(f"{ATOM}link[@rel='alternate']")
        if link_el is None:
            link_el = entry.find(f"{ATOM}link")
        link = (link_el.get("href") if link_el is not None else "") or ""
        title = _clean(entry.findtext(f"{ATOM}title"), 200)
        if title and link.startswith("http"):
            out.append(
                FeedItem(
                    title=title,
                    url=link,
                    publisher=publisher,
                    language=language,
                    summary=_clean(
                        entry.findtext(f"{ATOM}summary") or entry.findtext(f"{ATOM}content")
                    ),
                    published=_time(
                        entry.findtext(f"{ATOM}published") or entry.findtext(f"{ATOM}updated")
                    ),
                )
            )
    return out


@dataclass
class FeedCache:
    """All feeds, fetched together and kept for an hour."""

    http: httpx.AsyncClient
    feeds: tuple[tuple[str, str, str], ...] = FEEDS
    _items: tuple[float, list[FeedItem]] | None = None
    _lock: asyncio.Lock | None = None

    async def _one(self, publisher: str, url: str, language: str) -> list[FeedItem]:
        try:
            resp = await self.http.get(url, timeout=10, follow_redirects=True)
            resp.raise_for_status()
            return parse_feed(resp.text, publisher, language)[:20]
        except Exception as exc:  # one broken feed never breaks the tab
            log.warning("feed_failed", publisher=publisher, error=type(exc).__name__)
            return []

    async def items(self) -> list[FeedItem]:
        self._lock = self._lock or asyncio.Lock()
        async with self._lock:
            if self._items and time.time() - self._items[0] < FEED_TTL_S and self._items[1]:
                return self._items[1]
            batches = await asyncio.gather(*(self._one(*f) for f in self.feeds))
            items = [i for batch in batches for i in batch]
            items.sort(key=lambda i: i.published or 0, reverse=True)
            self._items = (time.time(), items)
            return items


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w{4,}", text.lower())}


def _by_language(items: list[FeedItem], language: str | None) -> list[FeedItem]:
    """The reader's language first, then English, then the rest (stable within each)."""
    base = (language or "en").split("-")[0].lower()
    return sorted(items, key=lambda i: 0 if i.language == base else 1 if i.language == "en" else 2)


# Without the AI: a verdict only when the headline or summary says it outright.
_MISLEADING = re.compile(
    r"\b(misleading|missing context|out of context|exaggerat\w*|distort\w*|spins?|half[- ]true)\b",
    re.I,
)
_FALSE = re.compile(
    r"\b(fake|false|falsely|hoax|no evidence|debunk\w*|fabricated|doctored|ai[- ]generated|"
    r"altered|misattributed|baseless|not real)\b",
    re.I,
)


def guess(item: FeedItem, why: str = "") -> Recommendation:
    text = f"{item.title} {item.summary}"
    verdict: Verdict = (
        "misleading" if _MISLEADING.search(text) else "false" if _FALSE.search(text) else ""
    )
    return Recommendation(**item.model_dump(), why=why, verdict=verdict, truth=item.summary)


def common_myths(recent: list[str], topics: list[str]) -> list[Recommendation]:
    """Every common myth, the ones closest to what this person checks first."""
    wanted = set().union(*(_words(r) for r in recent)) if recent else set()
    wanted |= {t.lower() for t in topics}

    def score(m: Myth) -> int:
        return len(wanted & (set(m.words) | _words(m.myth) | {m.topic.lower()}))

    ranked = sorted(MYTHS, key=score, reverse=True)  # stable: list order breaks ties
    return [
        Recommendation(
            title=m.myth,
            url=m.url,
            publisher=m.publisher,
            language="en",
            myth=m.myth,
            verdict="false",
            truth=m.truth,
            why=m.topic,
        )
        for m in ranked
    ]


def latest(items: list[FeedItem], language: str | None) -> ForYou:
    picked = _by_language(items, language)[:MAX_ITEMS]
    return ForYou(topics=[], items=[guess(i) for i in picked], myths=common_myths([], []))


def overlap_ranking(items: list[FeedItem], recent: list[str], language: str | None) -> ForYou:
    """Fallback without the AI: items sharing the most words with recent checks."""
    wanted = set().union(*(_words(r) for r in recent)) if recent else set()
    scored = [(len(wanted & _words(f"{i.title} {i.summary}")), i) for i in items]
    hits = [i for score, i in sorted(scored, key=lambda s: -s[0]) if score >= 2]
    if not hits:
        out = latest(items, language)
        out.myths = common_myths(recent, [])
        return out
    return ForYou(
        topics=[],
        items=[guess(i) for i in hits[:MAX_ITEMS]],
        myths=common_myths(recent, []),
    )


async def recommend(
    items: list[FeedItem], recent: list[str], language: str | None, llm: StructuredLLM | None
) -> ForYou:
    """Fact-checks as myth busters: the myth, the verdict, the truth, and the article.

    With no history the AI picks widely shared rumors; with history, ones like what was checked.
    The AI only restates what each article's headline and summary say; it never adds facts.
    """
    recent = [r.strip()[:300] for r in recent if r.strip()][:10]
    if not items:
        return ForYou(topics=[], items=[], myths=common_myths(recent, []))
    if llm is None:
        return overlap_ranking(items, recent, language) if recent else latest(items, language)
    pool = _by_language(items, language)[:60]
    listing = "\n".join(
        f"{n}. [{i.publisher}] {i.title}\n   {_clean(i.summary, 200)}" for n, i in enumerate(pool)
    )
    checked = "\n".join(f"- {r}" for r in recent) or "(nothing yet)"
    try:
        async with asyncio.timeout(AI_BUDGET_S):  # a busy AI never keeps the tab waiting
            out = await llm.structured(
                stage="for_you",
                system=prompts.render("for_you.v1"),
                user=(
                    f"Reader's language: {language or 'en'}\n\n<checked>\n{checked}\n</checked>\n\n"
                    f"<fact_checks>\n{listing}\n</fact_checks>"
                ),
                output=_Picks,
                effort="low",
                max_tokens=3000,
            )
    except Exception as exc:
        log.warning("for_you_llm_failed", error=type(exc).__name__)
        return overlap_ranking(items, recent, language) if recent else latest(items, language)
    seen: set[int] = set()
    picked: list[Recommendation] = []
    for pick in out.picks:
        if 0 <= pick.index < len(pool) and pick.index not in seen:
            seen.add(pick.index)
            item = pool[pick.index]
            fallback = guess(item)
            picked.append(
                Recommendation(
                    **item.model_dump(),
                    why=pick.why.strip(),
                    myth=pick.myth.strip(),
                    # A verdict needs a myth to be about; otherwise keep only what the text says.
                    verdict=pick.verdict if pick.myth.strip() else fallback.verdict,
                    truth=pick.truth.strip() or item.summary,
                )
            )
    if not picked:
        return overlap_ranking(items, recent, language) if recent else latest(items, language)
    topics = [t.strip() for t in out.topics if t.strip()][:4] if recent else []
    return ForYou(topics=topics, items=picked[:MAX_ITEMS], myths=common_myths(recent, topics))
