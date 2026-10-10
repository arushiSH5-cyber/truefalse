"""Parse schema.org ClaimReview data (DataFeed files, JSON-LD lists, Fact Check API results).

Data Commons publishes ClaimReview markup in DataFeed format:
https://datacommons.org/factcheck/download
"""

from collections.abc import Iterable, Iterator
from datetime import date
from typing import Any

from pydantic import BaseModel

from app.research.tiers import domain_of


class FactCheckRecord(BaseModel):
    review_url: str
    claim_text: str
    claim_language: str | None = None
    claimant: str | None = None
    claim_date: date | None = None
    publisher: str | None = None
    publisher_site: str | None = None
    rating: str | None = None
    review_title: str | None = None
    review_date: date | None = None


def _date(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else value


def _name(value: Any) -> str | None:
    value = _first(value)
    if isinstance(value, dict):
        return value.get("name") or None
    return value if isinstance(value, str) and value else None


def _lang(value: Any) -> str | None:
    value = _first(value)
    if isinstance(value, dict):
        value = value.get("alternateName") or value.get("name")
    return value.lower() if isinstance(value, str) and 2 <= len(value) <= 16 else None


def from_claimreview(item: dict[str, Any]) -> FactCheckRecord | None:
    """One schema.org ClaimReview object -> record (None if it lacks the essentials)."""
    url = item.get("url")
    claim = item.get("claimReviewed")
    if not isinstance(url, str) or not isinstance(claim, str) or not claim.strip():
        return None
    rating = _first(item.get("reviewRating")) or {}
    reviewed = _first(item.get("itemReviewed")) or {}
    author = _first(item.get("author")) or {}
    return FactCheckRecord(
        review_url=url,
        claim_text=claim.strip(),
        claim_language=_lang(item.get("inLanguage")),
        claimant=_name(reviewed.get("author")) if isinstance(reviewed, dict) else None,
        claim_date=_date(reviewed.get("datePublished")) if isinstance(reviewed, dict) else None,
        publisher=_name(author),
        publisher_site=domain_of(author.get("url", "")) if isinstance(author, dict) else None,
        rating=(rating.get("alternateName") or rating.get("name"))
        if isinstance(rating, dict)
        else None,
        review_title=item.get("name") or item.get("headline"),
        review_date=_date(item.get("datePublished")),
    )


def _claimreviews(node: Any) -> Iterator[dict[str, Any]]:
    """Walk DataFeed / ItemList / plain lists and yield every ClaimReview object."""
    if isinstance(node, list):
        for x in node:
            yield from _claimreviews(x)
    elif isinstance(node, dict):
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]
        if "ClaimReview" in types:
            yield node
            return
        for key in ("dataFeedElement", "item", "itemListElement", "@graph"):
            if key in node:
                yield from _claimreviews(node[key])


def parse_feed(data: Any) -> Iterator[FactCheckRecord]:
    for item in _claimreviews(data):
        record = from_claimreview(item)
        if record is not None:
            yield record


def from_factcheck_api(data: dict[str, Any]) -> Iterator[FactCheckRecord]:
    """Google Fact Check Tools API claims:search response -> records."""
    for claim in data.get("claims", []):
        for review in claim.get("claimReview", []):
            if not review.get("url") or not claim.get("text"):
                continue
            publisher = review.get("publisher") or {}
            yield FactCheckRecord(
                review_url=review["url"],
                claim_text=claim["text"],
                claim_language=review.get("languageCode"),
                claimant=claim.get("claimant"),
                claim_date=_date(claim.get("claimDate")),
                publisher=publisher.get("name"),
                publisher_site=publisher.get("site"),
                rating=review.get("textualRating"),
                review_title=review.get("title"),
                review_date=_date(review.get("reviewDate")),
            )


def dedupe(records: Iterable[FactCheckRecord]) -> list[FactCheckRecord]:
    seen: dict[str, FactCheckRecord] = {}
    for r in records:
        seen.setdefault(r.review_url, r)
    return list(seen.values())
