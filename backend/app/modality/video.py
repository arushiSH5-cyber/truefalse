"""Video: pull the audio track and a few keyframes with ffmpeg (no shell, hard timeouts)."""

import asyncio
import json
from pathlib import Path

FFMPEG_TIMEOUT_S = 120
MAX_KEYFRAMES = 4


class MediaToolError(Exception):
    pass


async def _run(*args: str, timeout_s: float = FFMPEG_TIMEOUT_S) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise MediaToolError(f"{args[0]} timed out") from None
    if proc.returncode != 0:
        raise MediaToolError(f"{args[0]} failed: {err.decode(errors='replace')[-300:]}")
    return out


async def probe_duration(path: Path) -> float | None:
    out = await _run(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
        timeout_s=30,
    )
    try:
        return float(json.loads(out)["format"]["duration"])
    except (KeyError, ValueError, TypeError):
        return None


async def has_audio(path: Path) -> bool:
    out = await _run(
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index",
        "-of",
        "json",
        str(path),
        timeout_s=30,
    )
    return bool(json.loads(out).get("streams"))


async def extract_audio(video: Path, out_dir: Path, max_seconds: float) -> Path | None:
    """16 kHz mono WAV (what Whisper expects). None if the video has no audio."""
    if not await has_audio(video):
        return None
    out = out_dir / f"{video.stem}.wav"
    await _run(
        "ffmpeg",
        "-nostdin",
        "-y",
        "-i",
        str(video),
        "-t",
        str(max_seconds),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(out),
    )
    return out


async def extract_keyframes(video: Path, out_dir: Path, count: int = MAX_KEYFRAMES) -> list[Path]:
    """Evenly spaced frames: robust for short social videos where scene detection is noisy."""
    duration = await probe_duration(video) or 0.0
    frames: list[Path] = []
    for i in range(count):
        t = duration * (i + 0.5) / count if duration else 0.0
        out = out_dir / f"{video.stem}_frame{i}.jpg"
        try:
            await _run(
                "ffmpeg",
                "-nostdin",
                "-y",
                "-ss",
                f"{t:.2f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(out),
                timeout_s=30,
            )
        except MediaToolError:
            continue
        if out.exists() and out.stat().st_size > 0:
            frames.append(out)
        if not duration:
            break
    return frames
