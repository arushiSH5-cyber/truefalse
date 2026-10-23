"""Score AI-content detection separately from claim verdicts.

Put labeled media in a folder with one subfolder per label (`ai/` and `real/`); images, video
and audio may be mixed. The detectors configured in .env run on every file (Content
Credentials, metadata, Sightengine/Hive/local classifiers, reverse image search for images).

Reported, overall and per media kind:
- `warned`: the system would warn users ("likely" or "possibly" AI). Precision, recall, F1
  and false-alarm rate (real files flagged) are computed from this.
- per detector: how often it said "AI-made" on AI files and on real files, weak hints
  included, to choose which detectors to trust.
The vision model's own "looks AI-made" hint (from image reading) is not part of this scorer.

Usage (from backend/):
  uv run python -m eval.ai_detect eval/datasets/ai_media --out eval/results/ai-media.json
"""

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from app.ai_detect.detector import AIDetector
from app.ai_detect.signals import AIReport, AISignal, MediaKind
from app.modality.media import temp_workdir
from app.modality.video import extract_frames

LABELS = ("ai", "real")
KINDS: dict[str, MediaKind] = {
    **dict.fromkeys((".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif"), "image"),
    **dict.fromkeys((".mp4", ".mov", ".webm", ".mkv"), "video"),
    **dict.fromkeys((".mp3", ".wav", ".ogg", ".oga", ".m4a", ".opus", ".flac"), "audio"),
}
WARNED = ("likely_ai", "possibly_ai")


class MediaItem(BaseModel):
    path: str
    label: str  # ai | real
    media: MediaKind


class MediaResult(BaseModel):
    item: MediaItem
    overall: str
    signals: list[AISignal]
    error: str | None = None

    @property
    def warned(self) -> bool:
        return self.overall in WARNED


def find_media(folder: Path) -> list[MediaItem]:
    out = []
    for label in LABELS:
        for path in sorted((folder / label).rglob("*")):
            media = KINDS.get(path.suffix.lower())
            if media and path.is_file():
                out.append(MediaItem(path=str(path), label=label, media=media))
    return out


async def check_one(detector: AIDetector, item: MediaItem) -> MediaResult:
    path = Path(item.path)
    try:
        if item.media == "image":
            signals, _ = await detector.check_image(path)
        elif item.media == "video":
            with temp_workdir() as work:
                frames = await extract_frames(path, work)
                signals, _ = await detector.check_video(path, [f.path for f in frames])
        else:
            signals = await detector.check_audio(path)
    except Exception as exc:
        return MediaResult(item=item, overall="no_signal", signals=[], error=str(exc)[:200])
    return MediaResult(item=item, overall=AIReport(signals=signals).overall, signals=signals)


def summarize(results: Sequence[MediaResult]) -> dict[str, object]:
    ai = [r for r in results if r.item.label == "ai"]
    real = [r for r in results if r.item.label == "real"]
    tp = sum(r.warned for r in ai)
    fp = sum(r.warned for r in real)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / len(ai) if ai else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    detectors: dict[str, dict[str, int]] = defaultdict(lambda: {"on_ai": 0, "on_real": 0})
    for r in results:
        fired = {s.detector for s in r.signals if s.finding == "ai_generated"}
        for d in fired:
            detectors[d]["on_ai" if r.item.label == "ai" else "on_real"] += 1
    return {
        "n_ai": len(ai),
        "n_real": len(real),
        "errors": sum(1 for r in results if r.error),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_alarm_rate": fp / len(real) if real else None,
        "detectors": {
            d: {
                **v,
                "recall": v["on_ai"] / len(ai) if ai else None,
                "false_alarm_rate": v["on_real"] / len(real) if real else None,
            }
            for d, v in sorted(detectors.items())
        },
    }


def report(results: Sequence[MediaResult]) -> dict[str, object]:
    by_kind: dict[str, list[MediaResult]] = defaultdict(list)
    for r in results:
        by_kind[r.item.media].append(r)
    return {
        "overall": summarize(results),
        "by_media": {k: summarize(v) for k, v in sorted(by_kind.items())},
    }


async def run(
    detector: AIDetector, items: Sequence[MediaItem], concurrency: int
) -> list[MediaResult]:
    sem = asyncio.Semaphore(concurrency)

    async def one(item: MediaItem) -> MediaResult:
        async with sem:
            return await check_one(detector, item)

    return list(await asyncio.gather(*(one(i) for i in items)))


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--concurrency", type=int, default=2)
    args = ap.parse_args(argv)

    from app.core.config import get_settings
    from app.core.container import build_ai, build_http_client

    items = find_media(args.folder)
    if not items:
        ap.error(f"no media in {args.folder}/ai or {args.folder}/real")
    settings = get_settings()

    async def go() -> list[MediaResult]:
        async with build_http_client(settings) as http:
            detector = build_ai(settings, http)
            if detector is None:
                raise SystemExit("AI_DETECT_ENABLED is false")
            return await run(detector, items, args.concurrency)

    rep = report(asyncio.run(go()))
    text = json.dumps(rep, indent=1)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
