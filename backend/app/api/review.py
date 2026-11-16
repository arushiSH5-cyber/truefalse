"""Reviewer endpoints for the human review queue (see app/core/review.py).

All need "Authorization: Bearer <REVIEW_TOKEN>". Without REVIEW_TOKEN they answer 503.

GET  /api/review?status=open|resolved|all&limit=50   items, newest first
GET  /api/review/stats                               thumbs up/down counts, open items
POST /api/review/{id}   {"correct_verdict": "false", "note": "..."}  resolve an item
GET  /api/review/export.jsonl                        resolved items as eval data (own_forwards)
"""

import hmac
import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.api.telegram import get_reviews
from app.core.config import Settings, get_settings
from app.core.models import Verdict
from app.core.review import FeedbackStats, ReviewItem, ReviewStore, as_eval_row

router = APIRouter(prefix="/api/review", tags=["review"])


def require_reviewer(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    if settings.review_token is None:
        raise HTTPException(503, "Review queue is not configured (set REVIEW_TOKEN)")
    expected = f"Bearer {settings.review_token.get_secret_value()}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(401, "Reviewer token required")


Reviewer = Depends(require_reviewer)


@router.get("", dependencies=[Reviewer])
async def list_items(
    reviews: Annotated[ReviewStore, Depends(get_reviews)],
    status: Literal["open", "resolved", "all"] = "open",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[ReviewItem]:
    return await reviews.list(status, limit)


@router.get("/stats", dependencies=[Reviewer])
async def stats(reviews: Annotated[ReviewStore, Depends(get_reviews)]) -> FeedbackStats:
    return await reviews.stats()


@router.get("/export.jsonl", dependencies=[Reviewer])
async def export(reviews: Annotated[ReviewStore, Depends(get_reviews)]) -> Response:
    rows = [r for i in await reviews.list("resolved", 500) if (r := as_eval_row(i))]
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    return Response(body, media_type="application/x-ndjson")


class Resolution(BaseModel):
    correct_verdict: Verdict
    note: str = Field(default="", max_length=2000)


@router.post("/{item_id}", dependencies=[Reviewer])
async def resolve(
    item_id: str, body: Resolution, reviews: Annotated[ReviewStore, Depends(get_reviews)]
) -> ReviewItem:
    item = await reviews.get(item_id) if item_id.isalnum() else None
    if item is None:
        raise HTTPException(404, "Not found or expired")
    item.status = "resolved"
    item.correct_verdict = body.correct_verdict
    item.reviewer_note = body.note.strip() or None
    await reviews.save(item)
    return item
