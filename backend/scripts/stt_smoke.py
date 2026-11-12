"""Transcribe every audio file in a folder with the real speech-to-text, to eyeball quality.

    uv sync --extra media
    uv run python -m scripts.stt_smoke path/to/voice_notes --hint es --model small

`--hint` simulates the user's phone UI language; the detected language should still follow the
audio. Runs locally; nothing is uploaded.
"""

import argparse
import asyncio
import time
from pathlib import Path

from app.modality.audio import FasterWhisperSTT

AUDIO_SUFFIXES = {".ogg", ".opus", ".oga", ".mp3", ".m4a", ".wav", ".aac", ".webm", ".mp4"}


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--hint", default=None, help="UI language hint, e.g. es or hi-IN")
    parser.add_argument("--model", default="small", help="tiny|base|small|medium|large-v3")
    args = parser.parse_args()
    stt = FasterWhisperSTT(args.model)
    for path in sorted(p for p in args.folder.iterdir() if p.suffix.lower() in AUDIO_SUFFIXES):
        start = time.perf_counter()
        t = await stt.transcribe(str(path), args.hint)
        took = time.perf_counter() - start
        print(f"{path.name:30} {t.language or '?':5} {took:5.1f}s  {t.text.strip()}")


if __name__ == "__main__":
    asyncio.run(main())
