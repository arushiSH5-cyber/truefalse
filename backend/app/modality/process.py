"""Turn a Submission (text, images, voice, video, links) into ProcessedContent for claim extraction.

Every provider is optional: a missing one produces a user-facing note instead of a crash.
All downloaded media lives in a temp dir owned by the caller and is deleted afterwards.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx
from pydantic import BaseModel, Field

from app.core.llm import LLMClient
from app.core.logging import get_logger
from app.core.models import Part, PartKind, Platform, Submission
from app.modality.audio import chunk_segments, render_chunks, select_claim_chunks
from app.modality.base import SpeechToText
from app.modality.hashes import ImageHashStore, SeenImage
from app.modality.image import ImageAnalysis, ImageKind, analyze_images
from app.modality.links import (
    LinkContent,
    YtDlpDownloader,
    classify,
    fetch_article,
    fetch_x_post,
    find_urls,
)
from app.modality.media import compress_image, image_tiles, perceptual_hash
from app.modality.video import MediaToolError, extract_audio, extract_keyframes

log = get_logger(__name__)

MAX_LINKS = 2
MAX_IMAGES = 4
MAX_AUDIO_SECONDS = 3600.0

ProgressFn = Callable[[str], Awaitable[None]]


class MediaFetcher(Protocol):
    """Downloads a platform media reference (e.g. Telegram file_id) into dest_dir."""

    async def fetch(self, part: Part, dest_dir: Path) -> Path: ...


@dataclass
class MediaProviders:
    llm: LLMClient
    fetcher: MediaFetcher | None = None  # used for any platform without its own fetcher
    fetchers: dict[Platform, MediaFetcher] = field(default_factory=dict)
    stt: SpeechToText | None = None
    hashes: ImageHashStore | None = None
    http: httpx.AsyncClient | None = None
    downloader: YtDlpDownloader | None = None


class ProcessedContent(BaseModel):
    text: str
    warnings: list[str] = Field(default_factory=list)  # English; translated at compose time
    image_hashes: list[str] = Field(default_factory=list)
    seen_before: list[SeenImage] = Field(default_factory=list)
    transcript_language: str | None = None
    used_media: bool = False


@dataclass
class _Acc:
    texts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    hashes: list[str] = field(default_factory=list)
    seen: list[SeenImage] = field(default_factory=list)
    language: str | None = None
    used_media: bool = False

    def warn(self, text: str) -> None:
        if text not in self.warnings:
            self.warnings.append(text)


W_EDITED = "The picture or video may have been edited."
W_AI = "The picture or video may have been made or changed with AI."
W_TRANSCRIBED = "We turned the audio into text automatically, so small mistakes are possible."
W_MEDIA_FAILED = "We could not open part of what you sent, so we checked only the rest."
W_UNSUPPORTED = "This kind of file can't be checked on this server yet."


async def _images(paths: Sequence[Path], p: MediaProviders, acc: _Acc, label: str) -> None:
    """label "image": each path is its own picture; "video": paths are frames of one video."""
    paths = list(paths)[:MAX_IMAGES]
    if not paths:
        return
    for path in paths if label == "image" else paths[:1]:
        phash = perceptual_hash(path)
        acc.hashes.append(phash)
        if p.hashes and (seen := await p.hashes.lookup(phash)):
            acc.seen.append(seen)
    if label == "video":
        _record(acc, label, await analyze_images(p.llm, [compress_image(x) for x in paths]))
        return
    for path in paths:
        tiles = image_tiles(path)
        kind: ImageKind = "slices" if len(tiles) > 1 else "image"
        _record(acc, label, await analyze_images(p.llm, tiles, kind))


def _record(acc: _Acc, label: str, a: ImageAnalysis) -> None:
    if a.text_in_image.strip():
        acc.texts.append(f"[Text visible in the {label}]: {a.text_in_image.strip()}")
    acc.texts.append(f"[What the {label} shows]: {a.description}")
    if a.possible_editing_signs:
        acc.warn(W_EDITED)
    if a.possibly_ai_generated:
        acc.warn(W_AI)
    acc.language = acc.language or a.language


async def _audio(path: Path, p: MediaProviders, acc: _Acc, label: str, hint: str | None) -> None:
    if p.stt is None:
        acc.warn(W_UNSUPPORTED)
        return
    transcript = await p.stt.transcribe(str(path), hint)
    chunks = await select_claim_chunks(p.llm, chunk_segments(transcript.segments))
    if chunks:
        acc.texts.append(f"[{label} transcript, with timestamps]:\n{render_chunks(chunks)}")
        acc.warn(W_TRANSCRIBED)
    acc.language = acc.language or transcript.language


async def _video(path: Path, workdir: Path, p: MediaProviders, acc: _Acc, hint: str | None) -> None:
    try:
        audio = await extract_audio(path, workdir, MAX_AUDIO_SECONDS)
        frames = await extract_keyframes(path, workdir)
    except MediaToolError as exc:
        log.warning("video_failed", error=str(exc))
        acc.warn(W_MEDIA_FAILED)
        return
    if frames:
        await _images(frames, p, acc, "video")
    if audio:
        await _audio(audio, p, acc, "Video audio", hint)


async def _link(url: str, workdir: Path, p: MediaProviders, acc: _Acc, hint: str | None) -> None:
    kind = classify(url)
    content: LinkContent
    if kind == "media":
        if p.downloader is None:
            acc.warn(W_UNSUPPORTED)
            return
        content = await p.downloader.fetch(url, workdir)
    elif p.http is None:
        return
    elif kind == "post":
        content = await fetch_x_post(p.http, url)
    else:
        content = await fetch_article(p.http, url)
    header = f"[Linked {content.kind}{': ' + content.title if content.title else ''}"
    header += f" by {content.uploader}" if content.uploader else ""
    header += f", published {content.published}" if content.published else ""
    acc.texts.append(f"{header}]\n{content.text}".strip())
    if content.media_path:
        media = Path(content.media_path)
        if media.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"}:
            await _video(media, workdir, p, acc, hint)
        else:
            await _audio(media, p, acc, "Linked audio", hint)


async def process_submission(
    submission: Submission,
    p: MediaProviders,
    workdir: Path,
    progress: ProgressFn | None = None,
) -> ProcessedContent:
    acc = _Acc()
    hint = submission.language_hint
    user_text = "\n".join(x.text for x in submission.parts if x.kind is PartKind.TEXT and x.text)
    if user_text:
        acc.texts.append(user_text)

    media_parts = [x for x in submission.parts if x.kind is not PartKind.TEXT]
    urls = find_urls(user_text)[:MAX_LINKS]
    if (media_parts or urls) and progress:
        await progress("media")

    for part in media_parts:
        acc.used_media = True
        try:
            fetcher = p.fetchers.get(submission.platform) or p.fetcher
            if fetcher is None:
                acc.warn(W_UNSUPPORTED)
                continue
            path = await fetcher.fetch(part, workdir)
            if part.kind is PartKind.IMAGE:
                await _images([path], p, acc, "image")
            elif part.kind is PartKind.AUDIO:
                await _audio(path, p, acc, "Voice note", hint)
            elif part.kind is PartKind.VIDEO:
                await _video(path, workdir, p, acc, hint)
        except Exception as exc:
            log.warning("media_part_failed", kind=part.kind, error=str(exc))
            acc.warn(W_MEDIA_FAILED)

    for url in urls:
        acc.used_media = True
        try:
            await _link(url, workdir, p, acc, hint)
        except Exception as exc:
            log.warning(
                "link_failed", host=url.split("/")[2] if "//" in url else "", error=str(exc)
            )
            acc.warn(W_MEDIA_FAILED)

    return ProcessedContent(
        text="\n\n".join(acc.texts),
        warnings=acc.warnings,
        image_hashes=acc.hashes,
        seen_before=acc.seen,
        transcript_language=acc.language,
        used_media=acc.used_media,
    )
