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


# Below this, Whisper's own language guess is shaky and the hint may break the tie.
LOW_LID_CONFIDENCE = 0.5
SAMPLE_RATE = 16_000
# Voice notes up to this long get per-utterance language detection (see FasterWhisperSTT).
PER_UTTERANCE_MAX_S = SHORT_AUDIO_SECONDS
# Utterances shorter than this are merged with a neighbour: language ID on them is unreliable.
MIN_UTTERANCE_S = 1.5

Span = tuple[int, int]  # start, end in samples


def _decode(path: str) -> Any:
    from faster_whisper import decode_audio

    return decode_audio(path, sampling_rate=SAMPLE_RATE)


def _speech_spans(audio: Any) -> list[Span]:
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    stamps = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=300))
    return [(int(t["start"]), int(t["end"])) for t in stamps]


def merge_short(spans: Sequence[Span], min_len: int) -> list[Span]:
    """Join each span shorter than min_len onto its neighbour."""
    out: list[Span] = []
    for s, e in spans:
        if out and (out[-1][1] - out[-1][0] < min_len or e - s < min_len):
            out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


def group_by_language(
    spans: Sequence[Span], languages: Sequence[str | None], default: str | None
) -> list[tuple[int, int, str | None]]:
    """Consecutive spans in the same language become one piece (more context per decode)."""
    groups: list[tuple[int, int, str | None]] = []
    for (s, e), lang in zip(spans, languages, strict=True):
        lang = lang or default
        if groups and groups[-1][2] == lang:
            groups[-1] = (groups[-1][0], e, lang)
        else:
            groups.append((s, e, lang))
    return groups


def tie_break(
    language: str | None,
    confidence: float,
    ranked: Sequence[tuple[str, float]],
    hint: str | None,
) -> tuple[str | None, bool]:
    """(language, hint_used). The UI-language hint wins only if Whisper is unsure and the hint
    is among its top three guesses."""
    hint = (hint or "").split("-")[0].lower() or None
    if hint and hint != language and confidence < LOW_LID_CONFIDENCE:
        top = [code for code, _ in sorted(ranked, key=lambda x: -x[1])[:3]]
        if hint in top:
            return hint, True
    return language, False


class FasterWhisperSTT:
    """SpeechToText via faster-whisper. Needs the `media` extra.

    Language is always auto-detected; the platform's UI language (`language_hint`) often differs
    from a forwarded note's language, so it only breaks a tie (see `tie_break`).

    Code-switching: Whisper decodes a whole 30 s window in one language and silently *translates*
    speech in any other language (a Spanish+English note came back all Spanish, with "on
    Monday" turned into "per week"). So voice notes are split at pauses, each utterance's
    language is detected on its own, and each run of same-language utterances is transcribed
    in that language. Mixing inside one sentence is still decoded in a single language.
    """

    def __init__(
        self,
        model_size: str = "small",
        device: str = "auto",
        compute_type: str = "int8",
        model: Any = None,
    ) -> None:
        self._args = (model_size, device, compute_type)
        self._model: Any = model

    def _load(self) -> Any:
        if self._model is None:
            from faster_whisper import WhisperModel

            size, device, compute = self._args
            self._model = WhisperModel(size, device=device, compute_type=compute)
        return self._model

    def _pieces(self, model: Any, audio: Any, hint: str | None) -> tuple[str | None, list[Any]]:
        """(main language, [(start, end, language or None for auto)])."""
        detected, confidence, ranked = model.detect_language(audio, vad_filter=True)
        language, forced = tie_break(detected, confidence, ranked, hint)
        if len(audio) > PER_UTTERANCE_MAX_S * SAMPLE_RATE:
            # Long audio: Whisper detects per 30 s window by itself (multilingual).
            return language, [(0, len(audio), language if forced else None)]
        spans = merge_short(_speech_spans(audio), int(MIN_UTTERANCE_S * SAMPLE_RATE))
        if len(spans) <= 1:
            return language, [(0, len(audio), language)]
        per_span: list[str | None] = []
        for s, e in spans:
            lang, p, _ = model.detect_language(audio[s:e])
            per_span.append(lang if p >= LOW_LID_CONFIDENCE else None)
        return language, list(group_by_language(spans, per_span, language))

    def _run(self, path: str, language_hint: str | None) -> Transcript:
        model = self._load()
        audio = _decode(path)
        language, pieces = self._pieces(model, audio, language_hint)
        segments: list[TranscriptSegment] = []
        for start, end, lang in pieces:
            # No conditioning on earlier text: avoids Whisper's repetition loops on long audio.
            segs, _ = model.transcribe(
                audio[start:end],
                language=lang,
                multilingual=lang is None,
                vad_filter=True,
                beam_size=5,
                condition_on_previous_text=False,
            )
            offset = start / SAMPLE_RATE
            segments += [
                TranscriptSegment(start_s=x.start + offset, end_s=x.end + offset, text=x.text)
                for x in segs
            ]
        return Transcript(language=language, segments=segments)

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
