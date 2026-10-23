"""Runs every enabled AI-content check on one piece of media and collects the signals.

Checks are independent and best-effort: one failing never stops the others or the fact-check.
"""

import asyncio
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.ai_detect.classifiers import ScoreClassifier, to_signal
from app.ai_detect.credentials import ContentCredentialsCheck
from app.ai_detect.metadata import MetadataCheck
from app.ai_detect.reverse_image import ReverseImageSearch
from app.ai_detect.signals import AISignal, MediaKind
from app.core.logging import get_logger
from app.core.models import EvidenceDraft

log = get_logger(__name__)

MAX_CLASSIFIED_FRAMES = 4


@dataclass
class AIDetector:
    credentials: ContentCredentialsCheck | None = None
    metadata: MetadataCheck | None = field(default_factory=MetadataCheck)
    frame_classifiers: Sequence[ScoreClassifier] = ()
    voice_classifier: ScoreClassifier | None = None
    reverse_search: ReverseImageSearch | None = None
    text_signal: bool = False

    async def _safe(self, name: str, job: Awaitable[list[AISignal]]) -> list[AISignal]:
        try:
            return await job
        except Exception as exc:
            log.warning("ai_check_failed", check=name, error=str(exc))
            return []

    async def _file_checks(self, path: Path, media: MediaKind) -> list[AISignal]:
        jobs = []
        if self.credentials:
            jobs.append(self._safe("c2pa", self.credentials.check(path, media)))
        if self.metadata:
            jobs.append(self._safe("metadata", self.metadata.check(path, media)))
        return [s for found in await asyncio.gather(*jobs) for s in found]

    async def _classify(self, paths: Sequence[Path], media: MediaKind) -> list[AISignal]:
        async def one(c: ScoreClassifier) -> list[AISignal]:
            return to_signal(c.name, media, await c.score(paths))

        found = await asyncio.gather(*(self._safe(c.name, one(c)) for c in self.frame_classifiers))
        return [s for f in found for s in f]

    async def _reverse(
        self, path: Path, media: MediaKind
    ) -> tuple[list[AISignal], list[EvidenceDraft]]:
        if self.reverse_search is None:
            return [], []
        try:
            return await self.reverse_search.check(path, media)
        except Exception as exc:
            log.warning("ai_check_failed", check="reverse_image", error=str(exc))
            return [], []

    async def check_image(self, path: Path) -> tuple[list[AISignal], list[EvidenceDraft]]:
        files, classified, (recycled, evidence) = await asyncio.gather(
            self._file_checks(path, "image"),
            self._classify([path], "image"),
            self._reverse(path, "image"),
        )
        return files + classified + recycled, evidence

    async def check_video(
        self, path: Path, frames: Sequence[Path]
    ) -> tuple[list[AISignal], list[EvidenceDraft]]:
        """Credentials/metadata on the file; classifiers on frames; reverse search on the
        middle frame (finds the original of re-uploaded clips)."""
        step = max(1, len(frames) // MAX_CLASSIFIED_FRAMES)
        sample = list(frames)[::step][:MAX_CLASSIFIED_FRAMES]
        middle = frames[len(frames) // 2] if frames else None
        files, classified, (recycled, evidence) = await asyncio.gather(
            self._file_checks(path, "video"),
            self._classify(sample, "video") if sample else _none(),
            self._reverse(middle, "video") if middle else _pair(),
        )
        return files + classified + recycled, evidence

    async def check_audio(self, path: Path) -> list[AISignal]:
        signals = await self._file_checks(path, "audio")
        if self.voice_classifier is not None:
            vc = self.voice_classifier

            async def voice() -> list[AISignal]:
                return to_signal(vc.name, "audio", await vc.score([path]))

            signals += await self._safe(vc.name, voice())
        return signals


async def _none() -> list[AISignal]:
    return []


async def _pair() -> tuple[list[AISignal], list[EvidenceDraft]]:
    return [], []
