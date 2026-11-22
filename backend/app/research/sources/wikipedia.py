"""Wikipedia search: free, no key. General background when no web-search key is set.

The quote is the part of each article that matches the query, not its intro: judges may only
cite what the quote says. It is tier 3, so on its own it can never give High confidence.
Docs: https://www.mediawiki.org/wiki/API:Search
"""

import asyncio
import math
import re
from typing import Any

import httpx

from app.core.http import get_json
from app.core.logging import get_logger
from app.core.models import EvidenceDraft, SourceKind
from app.matching.bm25 import tokenize
from app.research.sources.base import clip
from app.research.tiers import tier_for_url

log = get_logger(__name__)

_EDITION = re.compile(r"^[a-z]{2,3}$")  # the edition becomes part of the host name
QUOTE_CHARS = 1000
PASSAGE_CHARS = 480


def _chunks(text: str) -> list[str]:
    """Paragraphs, with long ones cut at word boundaries. Section headings are skipped."""
    out: list[str] = []
    for line in text.split("\n"):
        rest = line.strip()
        if rest.startswith("=="):
            continue
        while len(rest) > PASSAGE_CHARS:
            cut = rest.rfind(". ", PASSAGE_CHARS // 2, PASSAGE_CHARS) + 1
            cut = cut or rest.rfind(" ", PASSAGE_CHARS // 2, PASSAGE_CHARS) + 1 or PASSAGE_CHARS
            out.append(rest[:cut].strip())
            rest = rest[cut:].strip()
        if rest:
            out.append(rest)
    return out


def best_passages(text: str, query: str) -> str:
    """The two passages with the rarest query words, in article order."""
    chunks = _chunks(text)
    if not chunks:
        return ""
    words = [set(tokenize(c)) for c in chunks]
    terms = set(tokenize(query))
    weight = {}
    for term in terms:
        if df := sum(term in w for w in words):
            weight[term] = math.log(1 + len(chunks) / df)
    scores = [sum(weight.get(t, 0.0) for t in terms & w) for w in words]
    ranked = sorted(range(len(chunks)), key=lambda i: -scores[i])
    top = sorted(i for i in ranked[:2] if scores[i] > 0) or [0]
    return clip(" … ".join(chunks[i] for i in top), QUOTE_CHARS)


class WikipediaSource:
    kind = SourceKind.WEB

    def __init__(self, client: httpx.AsyncClient, max_results: int = 3) -> None:
        self._client = client
        self._max = max_results

    async def search(self, query: str, language: str | None = None) -> list[EvidenceDraft]:
        base = (language or "en").split("-")[0].lower()
        edition = base if _EDITION.fullmatch(base) else "en"
        api = f"https://{edition}.wikipedia.org/w/api.php"
        # The agent writes site: filters and quotes for web search engines; here they match nothing.
        query = re.sub(r"\bsite:\S+", "", query).replace('"', " ").strip()
        shared: dict[str, Any] = {"action": "query", "format": "json", "formatversion": 2}
        found = await get_json(
            self._client,
            api,
            params=shared | {"list": "search", "srsearch": query, "srlimit": self._max},
        )
        ids = [p["pageid"] for p in (found.get("query") or {}).get("search", [])]
        # Full text is one article per request.
        pages = await asyncio.gather(
            *(
                get_json(
                    self._client,
                    api,
                    params=shared
                    | {"pageids": i, "prop": "extracts|info", "explaintext": 1, "inprop": "url"},
                )
                for i in ids
            ),
            return_exceptions=True,
        )
        out: list[EvidenceDraft] = []
        for data in pages:
            if isinstance(data, BaseException):
                log.warning("wikipedia_page_failed", error=type(data).__name__)
            else:
                out += parse_wikipedia(data, query)
        return out


def parse_wikipedia(data: dict[str, Any], query: str) -> list[EvidenceDraft]:
    return [
        EvidenceDraft(
            source=SourceKind.WEB,
            tier=tier_for_url(p["fullurl"]),
            title=p["title"],
            url=p["fullurl"],
            quote=best_passages(p["extract"], query),
            published=None,  # edit dates say nothing about when a statement was written
            publisher="Wikipedia",
        )
        for p in (data.get("query") or {}).get("pages", [])
        if p.get("extract") and p.get("fullurl")
    ]
