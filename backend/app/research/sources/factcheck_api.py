"""Google Fact Check Tools API (ClaimReview search).

Docs: https://developers.google.com/fact-check/tools/api/reference/rest/v1alpha1/claims/search
"""

from typing import Any

import httpx

from app.core.http import get_json
from app.core.models import EvidenceDraft, SourceKind
from app.research.sources.base import clip

URL = "https://factchecktools.googleapis.com/v1alpha1/claims:search"


class GoogleFactCheckSource:
    kind = SourceKind.FACTCHECK

    def __init__(self, client: httpx.AsyncClient, api_key: str, max_results: int = 5) -> None:
        self._client = client
        self._key = api_key
        self._max = max_results

    async def search(self, query: str, language: str | None = None) -> list[EvidenceDraft]:
        params: dict[str, Any] = {"query": query, "pageSize": self._max, "key": self._key}
        if language:
            params["languageCode"] = language
        data = await get_json(self._client, URL, params=params)
        return parse(data)


def parse(data: dict[str, Any]) -> list[EvidenceDraft]:
    out: list[EvidenceDraft] = []
    for claim in data.get("claims", []):
        for review in claim.get("claimReview", []):
            url = review.get("url")
            if not url:
                continue
            publisher = (review.get("publisher") or {}).get("name")
            rating = review.get("textualRating")
            quote = f'Claim reviewed: "{claim.get("text", "")}". Rating: {rating}.'
            out.append(
                EvidenceDraft(
                    source=SourceKind.FACTCHECK,
                    tier=2,
                    title=review.get("title") or claim.get("text", "")[:120],
                    url=url,
                    quote=clip(quote),
                    published=(review.get("reviewDate") or "")[:10] or None,
                    publisher=publisher,
                    rating=rating,
                )
            )
    return out
