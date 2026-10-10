"""Speech-to-text and long-audio handling.

Long recordings (podcasts, long videos) are split into ~1 minute chunks; a cheap LLM pass keeps
only the chunks that contain check-worthy claims, so claim extraction stays focused and cheap.
"""

import asyncio
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field

from app.core import prompts
from app.core.llm import LLMClient
from app.modality.base import Transcript, TranscriptSegment

CHUNK_SECONDS = 60.0
# Below this length the whole transcript goes straight to claim extraction.
SHORT_AUDIO_SECONDS = 240.0
MAX_KEPT_CHUNKS = 8


class FasterWhisperSTT:
    """SpeechToText via faster-whisper (multilingual; detects the language). Needs `media` extra."""

    def __init__(
        self, model_size: str = "small", device: str = "auto", compute_type: str = "int8"
    ) -> None:
        self._args = (model_size, device, compute_type)
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            from faster_whisper import WhisperModel

            size, device, compute = self._args
            self._model = WhisperModel(size, device=device, compute_type=compute)
        return self._model

    def _run(self, path: str, language_hint: str | None) -> Transcript:
        segments, info = self._load().transcribe(
            path, language=language_hint, vad_filter=True, beam_size=5
        )
        return Transcript(
            language=getattr(info, "language", None),
            segments=[
                TranscriptSegment(start_s=s.start, end_s=s.end, text=s.text) for s in segments
            ],
        )

    async def transcribe(self, audio_path: str, language_hint: str | None = None) -> Transcript:
        return await asyncio.to_thread(self._run, audio_path, language_hint)


class Chunk(BaseModel):
    index: int
    start_s: float
    end_s: float
    text: str


def chunk_segments(
    segments: Sequence[TranscriptSegment], seconds: float = CHUNK_SECONDS
) -> list[Chunk]:
    chunks: list[Chunk] = []
    current: list[TranscriptSegment] = []
    for seg in segments:
        if current and seg.end_s - current[0].start_s > seconds:
            chunks.append(_make_chunk(len(chunks), current))
            current = []
        current.append(seg)
    if current:
        chunks.append(_make_chunk(len(chunks), current))
    return chunks


def _make_chunk(index: int, segs: Sequence[TranscriptSegment]) -> Chunk:
    return Chunk(
        index=index,
        start_s=segs[0].start_s,
        end_s=segs[-1].end_s,
        text=" ".join(s.text.strip() for s in segs),
    )


def fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def render_chunks(chunks: Sequence[Chunk]) -> str:
    return "\n".join(f"[{fmt_time(c.start_s)}] {c.text}" for c in chunks)


class ChunkSelection(BaseModel):
    claim_chunk_indexes: list[int] = Field(
        description="Indexes of chunks containing check-worthy factual claims, most important first"
    )


async def select_claim_chunks(
    llm: LLMClient, chunks: Sequence[Chunk], limit: int = MAX_KEPT_CHUNKS
) -> list[Chunk]:
    if not chunks:
        return []
    if chunks[-1].end_s - chunks[0].start_s <= SHORT_AUDIO_SECONDS:
        return list(chunks)
    listing = "\n".join(f"{c.index}: {c.text[:600]}" for c in chunks)
    out = await llm.structured(
        stage="segment_filter",
        system=prompts.render("segment_filter.v1", limit=str(limit)),
        user=f"<transcript_chunks>\n{listing}\n</transcript_chunks>",
        output=ChunkSelection,
        effort="low",
        max_tokens=1000,
    )
    keep = [i for i in dict.fromkeys(out.claim_chunk_indexes) if 0 <= i < len(chunks)][:limit]
    return [chunks[i] for i in sorted(keep)]
