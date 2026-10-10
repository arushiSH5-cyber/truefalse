"""Text-to-speech replies (pluggable). Default: gTTS (`voice` extra) + ffmpeg -> OGG/Opus.

Telegram and WhatsApp voice notes expect OGG/Opus, so the MP3 from gTTS is converted.
"""

import asyncio
from functools import cache
from pathlib import Path

from app.modality.video import run_tool

MAX_TTS_CHARS = 900


@cache
def _gtts_languages() -> frozenset[str]:
    from gtts.lang import tts_langs

    return frozenset(tts_langs())


class GTTSVoice:
    """TextToSpeech using Google Translate's TTS voice via gTTS (~60 languages)."""

    def __init__(self, out_dir: Path) -> None:
        self._out_dir = out_dir

    def supports(self, language: str) -> bool:
        langs = _gtts_languages()
        return language in langs or language.split("-")[0] in langs

    def _mp3(self, text: str, language: str, path: Path) -> None:
        from gtts import gTTS

        lang = language if language in _gtts_languages() else language.split("-")[0]
        gTTS(text=text[:MAX_TTS_CHARS], lang=lang, slow=False).save(str(path))

    async def synthesize(self, text: str, language: str) -> str:
        if not self.supports(language):
            raise ValueError(f"no TTS voice for {language}")
        stem = self._out_dir / f"reply-{abs(hash((text, language)))}"
        mp3, ogg = stem.with_suffix(".mp3"), stem.with_suffix(".ogg")
        await asyncio.wait_for(asyncio.to_thread(self._mp3, text, language, mp3), 30)
        await run_tool(
            "ffmpeg",
            "-nostdin",
            "-y",
            "-i",
            str(mp3),
            "-c:a",
            "libopus",
            "-b:a",
            "32k",
            str(ogg),
            timeout_s=30,
        )
        mp3.unlink(missing_ok=True)
        return str(ogg)
