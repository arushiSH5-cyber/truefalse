"""Provider interfaces for media processing. Implementations are swappable via config.

To add a provider (e.g. a different speech-to-text service), implement the matching Protocol
and register it in app/core/container.py. See CONTRIBUTING.md. Image reading (OCR, description,
editing cues) is one vision call in app/modality/image.py.
"""

from typing import Protocol

from pydantic import BaseModel


class TranscriptSegment(BaseModel):
    start_s: float
    end_s: float
    text: str


class Transcript(BaseModel):
    language: str | None  # BCP-47, if the provider detects it
    segments: list[TranscriptSegment]

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments)


class SpeechToText(Protocol):
    """Audio file -> timestamped transcript. Default: faster-whisper (modality/audio.py)."""

    async def transcribe(self, audio_path: str, language_hint: str | None = None) -> Transcript: ...


class TextToSpeech(Protocol):
    """Text -> audio file path (OGG/Opus for voice notes). Default: gTTS (respond/tts.py)."""

    async def synthesize(self, text: str, language: str) -> str: ...
