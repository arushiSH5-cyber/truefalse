"""AI-written text: reported only as a weak signal.

Text detectors are unreliable (many false alarms on non-native and formal writing, worse
outside English), so this never reaches users on its own; it is kept in the report and API for
transparency and evaluation. Off by default (AI_TEXT_SIGNAL) because it costs an LLM call.
"""

from pydantic import BaseModel, Field

from app.ai_detect.signals import AISignal, Strength
from app.core import prompts
from app.core.llm import StructuredLLM
from app.core.logging import get_logger

log = get_logger(__name__)

MIN_CHARS = 200  # shorter texts carry too little signal
REPORT_SCORE = 0.7


class AITextAssessment(BaseModel):
    likelihood: float = Field(ge=0, le=1, description="0 = clearly human, 1 = clearly AI-written")
    cues: list[str] = Field(description="Concrete cues, at most three; empty if none")


async def check_text(llm: StructuredLLM, text: str) -> list[AISignal]:
    if len(text.strip()) < MIN_CHARS:
        return []
    try:
        a = await llm.structured(
            stage="ai_text",
            system=prompts.render("ai_text.v1"),
            user=f"<message>\n{text[:4000]}\n</message>",
            output=AITextAssessment,
            effort="low",
            max_tokens=1000,
        )
    except Exception as exc:
        log.info("ai_text_failed", error=str(exc))
        return []
    if a.likelihood < REPORT_SCORE:
        return []
    return [
        AISignal(
            detector="ai_text",
            media="text",
            finding="ai_generated",
            strength=Strength.WEAK,
            score=round(a.likelihood, 3),
            detail="The writing has some features of AI-generated text (weak signal only).",
        )
    ]
