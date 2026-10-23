"""Web (PWA / extension) checks: job status, results and temporary uploads, all in Redis with TTLs.

Nothing here is permanent: results expire after RESULT_TTL_S, uploads after UPLOAD_TTL_S or as
soon as the worker has read them (raw media is never kept).
"""

import json
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.ai_detect.signals import AISignal
from app.core.models import CheckReport, Part, ReplyTarget
from app.respond.compose import MAX_SOURCES

RESULT_TTL_S = 24 * 3600
UPLOAD_TTL_S = 15 * 60
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class PublicSource(BaseModel):
    title: str
    url: str
    publisher: str | None
    tier: int
    published: str | None


class PublicResult(BaseModel):
    """What the web app shows: the reply plus structured fields. No user data."""

    verdict: str
    verdict_label: str
    confidence: str
    language: str
    reply_text: str
    explanation: str  # in the reply language
    correction: str = ""  # short message to forward, ends with the top source link
    confidence_label: str = ""  # e.g. "Confianza: Alta"
    sources_heading: str = "Sources"
    forward_heading: str = "Forward this"
    warnings: list[str]
    sources: list[PublicSource]
    claims: list[str]
    needs_human_review: bool = False
    ai_overall: str = "no_signal"  # likely_ai | possibly_ai | recycled | edited | no_signal
    ai_signals: list[AISignal] = Field(default_factory=list)  # all of them, weak ones included


class JobRecord(BaseModel):
    id: str
    status: Literal["queued", "working", "done", "failed"] = "queued"
    messages: list[str] = Field(default_factory=list)
    result: PublicResult | None = None


def public_result(report: CheckReport) -> PublicResult:
    by_id = {e.id: e for e in report.evidence}
    cited = dict.fromkeys(e for j in report.judgements for e in j.cited_evidence_ids)
    sources = sorted((by_id[i] for i in cited if i in by_id), key=lambda e: e.tier)
    reply = report.reply
    lead = min(report.judgements, key=lambda j: j.claim_index, default=None)
    first_line = report.reply_text.split("\n", 1)[0]
    label = first_line.split(" ", 1)[1] if " " in first_line else report.overall.value
    label = reply.verdict_label if reply else label
    return PublicResult(
        verdict=report.overall.value,
        verdict_label=label,
        confidence=report.overall_confidence.value,
        language=report.reply_language,
        reply_text=report.reply_text,
        explanation=reply.explanation if reply else (lead.explanation if lead else ""),
        correction=report.correction_text,
        confidence_label=reply.confidence_label if reply else "",
        sources_heading=reply.sources_heading if reply else "Sources",
        forward_heading=reply.forward_heading if reply else "Forward this",
        warnings=report.warnings,
        sources=[
            PublicSource(
                title=e.title, url=e.url, publisher=e.publisher, tier=e.tier, published=e.published
            )
            for e in sources[:MAX_SOURCES]
        ],
        claims=[c.original for c in report.claims],
        needs_human_review=report.needs_human_review,
        ai_overall=report.ai_report.overall,
        ai_signals=report.ai_report.signals,
    )


class JobStore(Protocol):
    async def create(self) -> JobRecord: ...

    async def get(self, job_id: str) -> JobRecord | None: ...

    async def update(self, record: JobRecord) -> None: ...

    async def put_upload(self, data: bytes, filename: str) -> str: ...

    async def take_upload(self, key: str) -> tuple[bytes, str] | None: ...


class InMemoryJobStore:
    def __init__(self) -> None:
        self.jobs: dict[str, JobRecord] = {}
        self.uploads: dict[str, tuple[bytes, str]] = {}

    async def create(self) -> JobRecord:
        rec = JobRecord(id=uuid4().hex)
        self.jobs[rec.id] = rec
        return rec.model_copy()

    async def get(self, job_id: str) -> JobRecord | None:
        rec = self.jobs.get(job_id)
        return rec.model_copy(deep=True) if rec else None

    async def update(self, record: JobRecord) -> None:
        self.jobs[record.id] = record.model_copy(deep=True)

    async def put_upload(self, data: bytes, filename: str) -> str:
        key = uuid4().hex
        self.uploads[key] = (data, filename)
        return key

    async def take_upload(self, key: str) -> tuple[bytes, str] | None:
        return self.uploads.pop(key, None)


class RedisJobStore:
    def __init__(self, redis_url: str) -> None:
        self._redis = Redis.from_url(redis_url, socket_timeout=5, socket_connect_timeout=3)

    async def create(self) -> JobRecord:
        rec = JobRecord(id=uuid4().hex)
        await self.update(rec)
        return rec

    async def get(self, job_id: str) -> JobRecord | None:
        raw = await self._redis.get(f"job:{job_id}")
        return JobRecord.model_validate_json(raw) if raw else None

    async def update(self, record: JobRecord) -> None:
        await self._redis.set(f"job:{record.id}", record.model_dump_json(), ex=RESULT_TTL_S)

    async def put_upload(self, data: bytes, filename: str) -> str:
        key = uuid4().hex
        meta = json.dumps({"filename": Path(filename).name[:100]})
        async with self._redis.pipeline() as pipe:
            pipe.set(f"upload:{key}", data, ex=UPLOAD_TTL_S)
            pipe.set(f"upload:{key}:meta", meta, ex=UPLOAD_TTL_S)
            await pipe.execute()
        return key

    async def take_upload(self, key: str) -> tuple[bytes, str] | None:
        async with self._redis.pipeline() as pipe:
            pipe.get(f"upload:{key}")
            pipe.get(f"upload:{key}:meta")
            pipe.delete(f"upload:{key}", f"upload:{key}:meta")
            data, meta, _ = await pipe.execute()
        if data is None:
            return None
        return data, json.loads(meta or "{}").get("filename", "upload.bin")

    async def close(self) -> None:
        await self._redis.aclose()


class WebMessenger:
    """Messenger for web checks: progress text goes into the job record the PWA polls."""

    def __init__(self, store: JobStore) -> None:
        self._store = store

    async def _append(self, target: ReplyTarget, text: str, status: Any = None) -> None:
        rec = await self._store.get(target.chat_id)
        if rec is None:
            return
        rec.messages.append(text)
        if status:
            rec.status = status
        await self._store.update(rec)

    async def send_text(self, target: ReplyTarget, text: str) -> None:
        await self._append(target, text, "working")

    async def send_typing(self, target: ReplyTarget) -> None:
        rec = await self._store.get(target.chat_id)
        if rec and rec.status == "queued":
            rec.status = "working"
            await self._store.update(rec)

    async def send_voice(self, target: ReplyTarget, ogg_path: str) -> None:
        return None  # the web app uses the browser's own speech synthesis

    async def finish(self, target: ReplyTarget, report: CheckReport | None) -> None:
        rec = await self._store.get(target.chat_id)
        if rec is None:
            return
        rec.status = "done" if report else "failed"
        rec.result = public_result(report) if report else None
        await self._store.update(rec)


class WebUploadFetcher:
    """MediaFetcher for web uploads (media_ref = upload key). Uploads are deleted on read."""

    def __init__(self, store: JobStore) -> None:
        self._store = store

    async def fetch(self, part: Part, dest_dir: Path) -> Path:
        got = await self._store.take_upload(part.media_ref or "")
        if got is None:
            raise ValueError("upload expired or missing")
        data, name = got
        dest = dest_dir / f"{uuid4().hex}{Path(name).suffix.lower()[:8]}"
        dest.write_bytes(data)  # noqa: ASYNC240 - local temp file
        return dest
