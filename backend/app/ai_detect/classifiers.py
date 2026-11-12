"""Classifiers that score how likely media is AI-generated or a deepfake.

Optional, pluggable, and treated as weak evidence: detectors trained on yesterday's generators
miss today's, and false alarms on real photos are common. A score only becomes a moderate
signal at >= STRONG_SCORE, and is never "strong".

- SightengineClassifier / HiveClassifier: paid APIs (images and video frames). They receive
  the user's media, so enable them only if your privacy policy allows it.
- HFImageClassifier / HFAudioClassifier: a local Hugging Face model you choose
  (`uv sync --extra ai_detect_local`). No default model: pick one and measure it with the eval.

Response formats follow the vendors' public docs; they have not been run against the live APIs.
"""

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.ai_detect.signals import AISignal, MediaKind, Strength
from app.core.logging import get_logger

log = get_logger(__name__)

STRONG_SCORE = 0.9
REPORT_SCORE = 0.5  # below this a classifier says nothing
AI_LABEL_WORDS = ("ai", "fake", "artificial", "generated", "synthetic", "deepfake", "spoof")
REAL_LABEL_WORDS = ("real", "human", "bonafide", "bona-fide", "authentic", "natural", "genuine")


class ScoreClassifier(Protocol):
    name: str

    async def score(self, paths: Sequence[Path]) -> float | None:
        """Highest AI/deepfake likelihood (0-1) over the given files, or None if unknown."""
        ...


def to_signal(detector: str, media: MediaKind, score: float | None) -> list[AISignal]:
    if score is None or score < REPORT_SCORE:
        return []
    strength = Strength.MODERATE if score >= STRONG_SCORE else Strength.WEAK
    return [
        AISignal(
            detector=detector,
            media=media,
            finding="ai_generated",
            strength=strength,
            score=round(score, 3),
            detail=f"An automatic detector rated this {media} {score:.0%} likely to be AI-made.",
        )
    ]


def ai_score_from_labels(results: Sequence[dict[str, Any]]) -> float | None:
    """Map a Hugging Face pipeline's [{label, score}] to P(AI), whatever the model's labels."""
    for r in results:
        words = str(r.get("label", "")).lower().replace("_", " ").replace("-", " ").split()
        if any(w in AI_LABEL_WORDS for w in words):
            return float(r["score"])
    for r in results:
        words = str(r.get("label", "")).lower().replace("_", " ").replace("-", " ").split()
        if any(w in REAL_LABEL_WORDS for w in words):
            return 1.0 - float(r["score"])
    return None


class SightengineClassifier:
    """https://sightengine.com/docs/ai-generated-image-detection (+ deepfake model)."""

    name = "sightengine"
    URL = "https://api.sightengine.com/1.0/check.json"

    def __init__(self, http: httpx.AsyncClient, api_user: str, api_secret: str) -> None:
        self._http = http
        self._auth = {"api_user": api_user, "api_secret": api_secret}

    async def _one(self, path: Path) -> float | None:
        content = await asyncio.to_thread(path.read_bytes)
        resp = await self._http.post(
            self.URL,
            data={"models": "genai,deepfake", **self._auth},
            files={"media": (path.name, content)},
        )
        resp.raise_for_status()
        return parse_sightengine(resp.json())

    async def score(self, paths: Sequence[Path]) -> float | None:
        scores = [s for s in await asyncio.gather(*(self._one(p) for p in paths)) if s is not None]
        return max(scores, default=None)


def parse_sightengine(data: dict[str, Any]) -> float | None:
    if data.get("status") != "success":
        return None
    kinds = data.get("type") or {}
    values = [
        float(v) for k, v in kinds.items() if k in ("ai_generated", "deepfake") and v is not None
    ]
    return max(values, default=None)


class HiveClassifier:
    """https://docs.thehive.ai/docs/ai-generated-image-and-video-detection"""

    name = "hive"
    URL = "https://api.thehive.ai/api/v2/task/sync"

    def __init__(self, http: httpx.AsyncClient, api_key: str) -> None:
        self._http = http
        self._headers = {"Authorization": f"Token {api_key}"}

    async def _one(self, path: Path) -> float | None:
        content = await asyncio.to_thread(path.read_bytes)
        resp = await self._http.post(
            self.URL, headers=self._headers, files={"media": (path.name, content)}
        )
        resp.raise_for_status()
        return parse_hive(resp.json())

    async def score(self, paths: Sequence[Path]) -> float | None:
        scores = [s for s in await asyncio.gather(*(self._one(p) for p in paths)) if s is not None]
        return max(scores, default=None)


def parse_hive(data: dict[str, Any]) -> float | None:
    values: list[float] = []
    for status in data.get("status") or []:
        for output in (status.get("response") or {}).get("output") or []:
            for c in output.get("classes") or []:
                if c.get("class") in ("ai_generated", "deepfake"):
                    values.append(float(c.get("score", 0.0)))
    return max(values, default=None)


class _HFPipeline:
    task = ""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self._pipe: Any = None

    def _run(self, path: Path) -> float | None:
        if self._pipe is None:
            from transformers import pipeline

            self._pipe = pipeline(self.task, model=self.model_id)
        return ai_score_from_labels(self._pipe(str(path)))

    async def score(self, paths: Sequence[Path]) -> float | None:
        results = await asyncio.gather(*(asyncio.to_thread(self._run, p) for p in paths))
        scores = [s for s in results if s is not None]
        return max(scores, default=None)


class HFImageClassifier(_HFPipeline):
    name = "local_image_model"
    task = "image-classification"


class HFAudioClassifier(_HFPipeline):
    name = "local_voice_model"
    task = "audio-classification"
