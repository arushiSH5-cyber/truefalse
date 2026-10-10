"""General web search. Provider: Brave Search API (swap by adding another class here).

Docs: https://api-dashboard.search.brave.com/app/documentation/web-search/get-started
"""

from typing import Any

import httpx

from app.core.http import get_json
from app.core.models import EvidenceDraft, SourceKind
from app.research.sources.base import clip
from app.research.tiers import domain_of, tier_for_url

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"


class BraveWebSearchSource:
    kind = SourceKind.WEB

    def __init__(self, client: httpx.AsyncClient, api_key: str, max_results: int = 6) -> None:
        self._client = client
        self._headers = {"X-Subscription-Token": api_key, "Accept": "application/json"}
        self._max = max_results

    async def search(self, query: str, language: str | None = None) -> list[EvidenceDraft]:
        params: dict[str, Any] = {"q": query, "count": self._max}
        if language:
            params["search_lang"] = language
        data = await get_json(self._client, BRAVE_URL, params=params, headers=self._headers)
        return parse_brave(data)


def parse_brave(data: dict[str, Any]) -> list[EvidenceDraft]:
    out: list[EvidenceDraft] = []
    for r in (data.get("web") or {}).get("results", []):
        url = r.get("url")
        if not url or not r.get("description"):
            continue
        out.append(
            EvidenceDraft(
                source=SourceKind.WEB,
                tier=tier_for_url(url),
                title=r.get("title") or url,
                url=url,
                quote=clip(r["description"]),
                published=r.get("page_age") or r.get("age"),
                publisher=domain_of(url),
            )
        )
    return out
