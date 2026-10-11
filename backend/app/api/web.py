"""Web API used by the PWA, the share target and the browser extension.

POST /api/check            multipart: text?, url?, file?   header X-Client-Id (random, anonymous)
GET  /api/check/{id}       job status, progress messages and the result
GET  /api/check/{id}/card.png   shareable correction card
Job ids are random 128-bit values, so a result URL is only known to whoever created it.
"""

import asyncio
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Response, UploadFile

from app.api.telegram import get_queue
from app.core.config import Settings, get_settings
from app.core.jobs import MAX_UPLOAD_BYTES, JobRecord, JobStore, RedisJobStore
from app.core.models import Part, PartKind, Platform, ReplyTarget, Submission
from app.core.ratelimit import RateLimiter, RedisRateLimiter
from app.ingestion.base import JobQueue
from app.ingestion.common import hash_user
from app.ingestion.handler import CHECK_JOB
from app.respond.share_card import render_card

router = APIRouter(prefix="/api", tags=["web"])

MEDIA_KINDS = {"image": PartKind.IMAGE, "audio": PartKind.AUDIO, "video": PartKind.VIDEO}
MAX_TEXT = 10_000


@lru_cache
def _jobs(redis_url: str) -> RedisJobStore:
    return RedisJobStore(redis_url)


@lru_cache
def _limiter(redis_url: str) -> RedisRateLimiter:
    return RedisRateLimiter(redis_url)


def get_jobs(settings: Annotated[Settings, Depends(get_settings)]) -> JobStore:
    return _jobs(settings.redis_url)


def get_limiter(settings: Annotated[Settings, Depends(get_settings)]) -> RateLimiter:
    return _limiter(settings.redis_url)


def _language_hint(accept_language: str | None) -> str | None:
    if not accept_language:
        return None
    first = accept_language.split(",")[0].split(";")[0].strip()
    return first or None


@router.post("/check", status_code=202)
async def create_check(
    settings: Annotated[Settings, Depends(get_settings)],
    jobs: Annotated[JobStore, Depends(get_jobs)],
    queue: Annotated[JobQueue, Depends(get_queue)],
    limiter: Annotated[RateLimiter, Depends(get_limiter)],
    client_id: Annotated[str, Header(alias="X-Client-Id", min_length=8, max_length=64)],
    text: Annotated[str, Form(max_length=MAX_TEXT)] = "",
    url: Annotated[str, Form(max_length=2000)] = "",
    file: Annotated[UploadFile | None, File()] = None,
    accept_language: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    salt = settings.user_hash_salt.get_secret_value()
    user_hash = hash_user(salt, "web", client_id)
    if not await limiter.allow(user_hash, settings.web_checks_per_hour):
        raise HTTPException(429, "Too many checks. Please try again later.")

    parts: list[Part] = []
    combined = "\n".join(x for x in (text.strip(), url.strip()) if x)
    if combined:
        parts.append(Part(kind=PartKind.TEXT, text=combined))
    if file is not None and file.filename:
        kind = MEDIA_KINDS.get((file.content_type or "").split("/")[0])
        if kind is None:
            raise HTTPException(415, "Send an image, audio or video file.")
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "File too large (max 20 MB).")
        key = await jobs.put_upload(data, Path(file.filename).name)
        parts.append(Part(kind=kind, media_ref=key))
    if not parts:
        raise HTTPException(422, "Send some text, a link, or a file to check.")

    job = await jobs.create()
    submission = Submission(
        platform=Platform.WEB,
        user_hash=user_hash,
        language_hint=_language_hint(accept_language),
        parts=parts,
    )
    target = ReplyTarget(platform=Platform.WEB, chat_id=job.id)
    await queue.enqueue(
        CHECK_JOB,
        {
            "submission": submission.model_dump(mode="json"),
            "target": target.model_dump(mode="json"),
        },
    )
    return {"id": job.id}


@router.get("/check/{job_id}", response_model=JobRecord)
async def get_check(job_id: str, jobs: Annotated[JobStore, Depends(get_jobs)]) -> JobRecord:
    rec = await jobs.get(job_id) if job_id.isalnum() else None
    if rec is None:
        raise HTTPException(404, "Not found or expired")
    return rec


@router.get("/check/{job_id}/card.png")
async def share_card(
    job_id: str,
    jobs: Annotated[JobStore, Depends(get_jobs)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    rec = await jobs.get(job_id) if job_id.isalnum() else None
    if rec is None or rec.result is None:
        raise HTTPException(404, "Not found or not ready")
    r = rec.result
    top = r.sources[0] if r.sources else None
    png = await asyncio.to_thread(
        render_card,
        verdict=r.verdict,
        verdict_label=r.verdict_label,
        claim=r.claims[0] if r.claims else "",
        explanation=r.explanation,
        source=(top.publisher or top.url) if top else None,
        font_dir=Path(settings.share_card_font_dir),
    )
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})
