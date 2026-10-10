"""Semantic Scholar Graph API paper search.

Docs: https://api.semanticscholar.org/api-docs/graph#tag/Paper-Data/operation/get_graph_paper_relevance_search
"""

from typing import Any

import httpx

from app.core.http import get_json
from app.core.models import EvidenceDraft, SourceKind
from app.research.sources.base import clip

URL = "https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS = "title,abstract,url,year,venue,tldr,publicationDate"


class SemanticScholarSource:
    kind = SourceKind.SEMANTIC_SCHOLAR

    def __init__(
        self, client: httpx.AsyncClient, api_key: str | None = None, max_results: int = 5
    ) -> None:
        self._client = client
        self._headers = {"x-api-key": api_key} if api_key else {}
        self._max = max_results

    async def search(self, query: str, language: str | None = None) -> list[EvidenceDraft]:
        data = await get_json(
            self._client,
            URL,
            params={"query": query, "limit": self._max, "fields": FIELDS},
            headers=self._headers,
        )
        return parse(data)


def parse(data: dict[str, Any]) -> list[EvidenceDraft]:
    out: list[EvidenceDraft] = []
    for paper in data.get("data", []) or []:
        url = paper.get("url")
        tldr = (paper.get("tldr") or {}).get("text")
        text = tldr or paper.get("abstract")
        if not url or not text:
            continue
        out.append(
            EvidenceDraft(
                source=SourceKind.SEMANTIC_SCHOLAR,
                tier=1,
                title=paper.get("title") or "Untitled paper",
                url=url,
                quote=clip(text),
                published=paper.get("publicationDate")
                or (str(paper["year"]) if paper.get("year") else None),
                publisher=paper.get("venue") or None,
            )
        )
    return out
