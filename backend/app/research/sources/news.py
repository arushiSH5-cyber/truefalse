"""News search, so recent events and quotes can be checked against live coverage.

- Brave News Search: same key as Brave web search.
  Docs: https://api-dashboard.search.brave.com/app/documentation/news-search/get-started
- GDELT DOC 2.0: free, no key, news in 65+ languages from most countries, last ~3 months.
  Docs: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
  Results have a headline but no snippet, so their quote is the headline only.
"""

import asyncio
import time
from typing import Any

import httpx

from app.core.http import get_json
from app.core.models import EvidenceDraft, SourceKind
from app.language.names import english_name
from app.research.sources.base import clip
from app.research.tiers import domain_of, tier_for_url

BRAVE_NEWS_URL = "https://api.search.brave.com/res/v1/news/search"
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


class BraveNewsSource:
    kind = SourceKind.NEWS

    def __init__(self, client: httpx.AsyncClient, api_key: str, max_results: int = 6) -> None:
        self._client = client
        self._headers = {"X-Subscription-Token": api_key, "Accept": "application/json"}
        self._max = max_results

    async def search(self, query: str, language: str | None = None) -> list[EvidenceDraft]:
        params: dict[str, Any] = {"q": query, "count": self._max}
        if language:
            params["search_lang"] = language
        data = await get_json(self._client, BRAVE_NEWS_URL, params=params, headers=self._headers)
        return parse_brave_news(data)


def parse_brave_news(data: dict[str, Any]) -> list[EvidenceDraft]:
    out: list[EvidenceDraft] = []
    for r in data.get("results") or []:
        url = r.get("url")
        if not url or not (r.get("description") or r.get("title")):
            continue
        out.append(
            EvidenceDraft(
                source=SourceKind.NEWS,
                tier=tier_for_url(url),
                title=r.get("title") or url,
                url=url,
                quote=clip(r.get("description") or r.get("title")),
                published=r.get("page_age") or r.get("age"),
                publisher=(r.get("meta_url") or {}).get("hostname") or domain_of(url),
            )
        )
    return out


class GdeltNewsSource:
    """GDELT asks for at most one request every 5 seconds; calls are spaced out here."""

    kind = SourceKind.NEWS
    MIN_INTERVAL_S = 5.0

    def __init__(self, client: httpx.AsyncClient, max_results: int = 8) -> None:
        self._client = client
        self._max = max_results
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def search(self, query: str, language: str | None = None) -> list[EvidenceDraft]:
        q = query
        if language and (name := english_name(language)) != language:
            q = f"{query} sourcelang:{name.lower()}"
        params = {
            "query": q,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": self._max,
            "sort": "HybridRel",
        }
        async with self._lock:
            wait = self._last + self.MIN_INTERVAL_S - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                data = await get_json(self._client, GDELT_URL, params=params)
            finally:
                self._last = time.monotonic()
        return parse_gdelt(data)


def _gdelt_date(seen: str | None) -> str | None:
    # "20240301T120000Z" -> "2024-03-01"
    if not seen or len(seen) < 8 or not seen[:8].isdigit():
        return None
    return f"{seen[:4]}-{seen[4:6]}-{seen[6:8]}"


def parse_gdelt(data: dict[str, Any]) -> list[EvidenceDraft]:
    out: list[EvidenceDraft] = []
    for a in data.get("articles") or []:
        url, title = a.get("url"), a.get("title")
        if not url or not title:
            continue
        where = ", ".join(x for x in (a.get("sourcecountry"), a.get("language")) if x)
        out.append(
            EvidenceDraft(
                source=SourceKind.NEWS,
                tier=tier_for_url(url),
                title=title,
                url=url,
                quote=clip(f"Headline: {title}" + (f" ({where})" if where else "")),
                published=_gdelt_date(a.get("seendate")),
                publisher=a.get("domain") or domain_of(url),
            )
        )
    return out
