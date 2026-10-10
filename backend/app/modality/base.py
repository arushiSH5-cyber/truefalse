"""Provider interfaces for media processing. Implementations are swappable via config.

Phase 5 wires these into the pipeline. To add a provider (e.g. a different OCR engine or
speech-to-text service), implement the matching Protocol in a new module under
app/modality/providers/ and register it in app/core/container.py. See CONTRIBUTING.md.
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


class OCRResult(BaseModel):
    text: str
    description: str | None = None  # what the image shows, beyond its text
    language: str | None = None


class SpeechToText(Protocol):
    """Audio file -> timestamped transcript. Default planned: faster-whisper."""

    async def transcribe(self, audio_path: str, language_hint: str | None = None) -> Transcript: ...


class ImageReader(Protocol):
    """Image/screenshot -> text + description. Default planned: Claude vision."""

    async def read(self, image_path: str) -> OCRResult: ...


class TextToSpeech(Protocol):
    """Text -> audio file path (OGG/Opus for voice notes). Default planned: gTTS."""

    async def synthesize(self, text: str, language: str) -> str: ...
