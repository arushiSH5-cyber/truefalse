"""AI-content and manipulation signals, and how they combine into what users are told.

Every detector reports `AISignal`s. Detectors are uneven: a signed Content Credential that says
"made with AI" is strong; a classifier score is weak (classifiers generalize poorly to new
generators); the vision model's impression is weaker still. Users only hear about strong or
corroborated signals. Weak ones are kept in the report and the API for transparency and eval.
"""

from collections.abc import Sequence
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Strength(StrEnum):
    STRONG = "strong"  # a direct statement: signed credentials, generator metadata, older copies
    MODERATE = "moderate"  # good but indirect evidence
    WEAK = "weak"  # a hint only; never shown to users on its own


Finding = Literal["ai_generated", "edited", "recycled", "camera_original", "no_finding"]
MediaKind = Literal["image", "video", "audio", "text"]


class AISignal(BaseModel):
    detector: str  # c2pa | metadata | frame_classifier | voice_classifier | reverse_image | ...
    media: MediaKind
    finding: Finding
    strength: Strength
    score: float | None = None  # detector's own 0-1 score, when it has one
    detail: str = ""  # short English, safe to show
    source_url: str | None = None  # e.g. the earliest page showing the same picture


Overall = Literal["likely_ai", "possibly_ai", "recycled", "edited", "no_signal"]

# Two different detectors each weakly saying "AI" count as one moderate signal.
CORROBORATING_WEAK = 2


def _ai(signals: Sequence[AISignal], strength: Strength) -> list[AISignal]:
    return [s for s in signals if s.finding == "ai_generated" and s.strength is strength]


class AIReport(BaseModel):
    signals: list[AISignal] = Field(default_factory=list)

    @property
    def overall(self) -> Overall:
        s = self.signals
        if _ai(s, Strength.STRONG):
            return "likely_ai"
        weak_detectors = {x.detector for x in _ai(s, Strength.WEAK)}
        if _ai(s, Strength.MODERATE) or len(weak_detectors) >= CORROBORATING_WEAK:
            return "possibly_ai"
        if any(x.finding == "recycled" and x.strength is not Strength.WEAK for x in s):
            return "recycled"
        if any(x.finding == "edited" and x.strength is not Strength.WEAK for x in s):
            return "edited"
        return "no_signal"

    def user_warnings(self) -> list[str]:
        """English warnings for the reply (translated later). Weak signals alone say nothing."""
        out: list[str] = []
        strong = _ai(self.signals, Strength.STRONG)
        if strong:
            out.append(
                strong[0].detail or "This content carries a label saying it was made with AI."
            )
        elif self.overall == "possibly_ai":
            out.append(W_POSSIBLY_AI)
        for s in self.signals:
            shown = s.finding in ("recycled", "edited") and s.strength is not Strength.WEAK
            if shown and s.detail and s.detail not in out:
                out.append(s.detail)
        return out


W_POSSIBLY_AI = (
    "Checks suggest this picture, video or voice may have been made or changed with AI. "
    "Treat it with care."
)
