"""Reverse image search for recycled photos ("this picture is from a 2015 flood, not today").

Provider: Google Cloud Vision web detection (API key, billed per image). It returns pages that
show the same picture but no dates, so the publish date of the top pages is read with the same
SSRF-safe article fetcher used for links. The pages also become evidence for the judges.
The user's image is sent to Google: enable only if your privacy policy allows it.
Docs: https://cloud.google.com/vision/docs/detecting-web
"""

import asyncio
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

from app.ai_detect.signals import AISignal, MediaKind, Strength
from app.core.logging import get_logger
from app.core.models import EvidenceDraft, SourceKind
from app.modality.links import fetch_article
from app.modality.media import compress_image
from app.research.sources.base import clip
from app.research.tiers import domain_of, tier_for_url

log = get_logger(__name__)

VISION_URL = "https://vision.googleapis.com/v1/images:annotate"
MAX_PAGES = 4
DATE_FETCH_TIMEOUT_S = 15.0
# A full match online this long before the check means the picture is not new.
RECYCLED_AFTER_DAYS = 30


class MatchPage(BaseModel):
    url: str
    title: str
    full_match: bool
    published: date | None = None


def parse_web_detection(data: dict[str, Any]) -> tuple[list[MatchPage], list[str]]:
    """(pages showing the picture, full matches first; best-guess labels)."""
    web = ((data.get("responses") or [{}])[0] or {}).get("webDetection") or {}
    pages: list[MatchPage] = []
    for p in web.get("pagesWithMatchingImages") or []:
        url = p.get("url")
        if not url or not url.startswith(("http://", "https://")):
            continue
        title = re.sub(r"<[^>]+>", "", p.get("pageTitle") or "") or domain_of(url)
        pages.append(MatchPage(url=url, title=title, full_match=bool(p.get("fullMatchingImages"))))
    pages.sort(key=lambda p: not p.full_match)
    labels = [x["label"] for x in web.get("bestGuessLabels") or [] if x.get("label")]
    return pages, labels


def _parse_date(value: str | None) -> date | None:
    m = re.search(r"((?:19|20)\d{2})-(\d{2})-(\d{2})", value or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


class ReverseImageSearch:
    def __init__(self, http: httpx.AsyncClient, api_key: str) -> None:
        self._http = http
        self._key = api_key

    async def _search(self, path: Path) -> dict[str, Any]:
        image = compress_image(path)  # smaller upload; matching is robust to resizing
        body = {
            "requests": [
                {
                    "image": {"content": image.data_b64},
                    "features": [{"type": "WEB_DETECTION", "maxResults": 20}],
                }
            ]
        }
        resp = await self._http.post(VISION_URL, params={"key": self._key}, json=body)
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        return result

    async def _date(self, page: MatchPage) -> MatchPage:
        try:
            article = await fetch_article(self._http, page.url)
        except Exception:
            return page
        return page.model_copy(update={"published": _parse_date(article.published)})

    async def check(
        self, path: Path, media: MediaKind, today: date | None = None
    ) -> tuple[list[AISignal], list[EvidenceDraft]]:
        pages, _ = parse_web_detection(await self._search(path))
        pages = pages[:MAX_PAGES]
        if not pages:
            return [], []
        try:
            async with asyncio.timeout(DATE_FETCH_TIMEOUT_S):
                pages = list(await asyncio.gather(*(self._date(p) for p in pages)))
        except TimeoutError:
            log.info("reverse_image_dates_timeout")
        evidence = [
            EvidenceDraft(
                source=SourceKind.WEB,
                tier=tier_for_url(p.url),
                title=p.title[:200],
                url=p.url,
                quote=clip(
                    ("The same picture" if p.full_match else "A similar picture")
                    + f" appears on this page{f', published {p.published}' if p.published else ''}"
                    f": {p.title}"
                ),
                published=p.published.isoformat() if p.published else None,
                publisher=domain_of(p.url),
            )
            for p in pages
        ]
        dated = sorted(
            ((p.published, p) for p in pages if p.full_match and p.published), key=lambda x: x[0]
        )
        today = today or datetime.now(UTC).date()
        if dated and (today - dated[0][0]).days >= RECYCLED_AFTER_DAYS:
            first = dated[0][1]
            signal = AISignal(
                detector="reverse_image",
                media=media,
                finding="recycled",
                strength=Strength.STRONG,
                detail=f"This picture was online before, at least since {first.published} "
                f"({domain_of(first.url)}). It may be old or from a different event.",
                source_url=first.url,
            )
            return [signal], evidence
        return [], evidence
