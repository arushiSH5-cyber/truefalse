"""Video: pull the audio track and keyframes with ffmpeg (no shell, hard timeouts).

Keyframes are a few evenly spaced frames plus the first frame after each scene cut, so short
social videos with fast cuts (and the captions on each shot) are covered.
"""

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path

FFMPEG_TIMEOUT_S = 120
EVEN_FRAMES = 4
MAX_KEYFRAMES = 8
SCENE_THRESHOLD = 0.3
# Scene detection decodes the whole video; only worth it for short clips.
SCENE_DETECT_MAX_S = 600.0
MIN_FRAME_GAP_S = 1.0
_PTS_TIME = re.compile(r"pts_time:\s*([0-9.]+)")


@dataclass(frozen=True)
class Frame:
    time_s: float
    path: Path


class MediaToolError(Exception):
    pass


async def run_tool(*args: str, timeout_s: float = FFMPEG_TIMEOUT_S) -> bytes:
    return (await run_tool_full(*args, timeout_s=timeout_s))[0]


async def run_tool_full(*args: str, timeout_s: float = FFMPEG_TIMEOUT_S) -> tuple[bytes, bytes]:
    """(stdout, stderr). ffmpeg's filter logs (e.g. showinfo) go to stderr."""
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
    return out, err


async def probe_duration(path: Path) -> float | None:
    out = await run_tool(
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
    out = await run_tool(
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
    await run_tool(
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


async def scene_times(video: Path, threshold: float = SCENE_THRESHOLD) -> list[float]:
    """Timestamps of scene cuts (on a small copy of the picture, for speed)."""
    _, err = await run_tool_full(
        "ffmpeg",
        "-nostdin",
        "-i",
        str(video),
        "-an",
        "-vf",
        f"scale=320:-2,select='gt(scene,{threshold})',showinfo",
        "-f",
        "null",
        "-",
    )
    return [float(t) for t in _PTS_TIME.findall(err.decode(errors="replace"))]


def pick_frame_times(
    duration: float,
    scenes: list[float],
    max_frames: int = MAX_KEYFRAMES,
    even: int = EVEN_FRAMES,
    min_gap: float = MIN_FRAME_GAP_S,
) -> list[float]:
    """Evenly spaced frames, plus a frame just after each scene cut that is not too close to
    one already picked; at most max_frames, spread over the video."""
    if duration <= 0:
        return [0.0]
    gap = min(min_gap, duration / (2 * even))  # short clips: smaller gap
    picked = [round(duration * (i + 0.5) / even, 2) for i in range(even)]
    for cut in sorted(scenes):
        t = round(min(cut + 0.3, duration - 0.05), 2)
        if 0 < cut < duration and all(abs(t - p) >= gap for p in picked):
            picked.append(t)
    picked.sort()
    if len(picked) > max_frames:
        keep = dict.fromkeys(
            round(i * (len(picked) - 1) / max(max_frames - 1, 1)) for i in range(max_frames)
        )
        picked = [picked[i] for i in keep]
    return picked


async def extract_frames(
    video: Path, out_dir: Path, max_frames: int = MAX_KEYFRAMES
) -> list[Frame]:
    duration = await probe_duration(video) or 0.0
    scenes: list[float] = []
    if 0 < duration <= SCENE_DETECT_MAX_S:
        try:
            scenes = await scene_times(video)
        except MediaToolError:
            scenes = []  # even sampling still works
    frames: list[Frame] = []
    for i, t in enumerate(pick_frame_times(duration, scenes, max_frames)):
        out = out_dir / f"{video.stem}_frame{i}.jpg"
        try:
            await run_tool(
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
            frames.append(Frame(t, out))
    return frames
