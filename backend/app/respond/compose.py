"""Compose the user-facing reply in the user's language.

The LLM only writes translatable text. Verdict emoji, source titles and URLs are inserted by
code, so links can never be invented.
"""

import json
from collections.abc import Sequence

from app.core import prompts
from app.core.llm import LLMClient
from app.core.logging import get_logger
from app.core.models import ComposedReply, Confidence, Evidence, Verdict

log = get_logger(__name__)

EMOJI = {
    Verdict.TRUE: "🟢",
    Verdict.MISLEADING: "🟠",
    Verdict.FALSE: "🔴",
    Verdict.UNVERIFIED: "⚪",
}
EN_LABEL = {
    Verdict.TRUE: "True",
    Verdict.MISLEADING: "Misleading",
    Verdict.FALSE: "False",
    Verdict.UNVERIFIED: "Unverified",
}
MAX_SOURCES = 3
MAX_CORRECTION_CHARS = 400
TELEGRAM_LIMIT = 4000


EN_CORRECTION = {
    Verdict.TRUE: "I checked this message: it is true.",
    Verdict.MISLEADING: "I checked this message: it is misleading.",
    Verdict.FALSE: "I checked this message: it is false.",
    Verdict.UNVERIFIED: "I checked this message, but it could not be confirmed. "
    "Please wait before sharing it.",
}


def english_reply(
    verdict: Verdict,
    confidence: Confidence,
    explanation: str,
    warnings: Sequence[str],
    safety_note: str | None,
) -> ComposedReply:
    return ComposedReply(
        verdict_label=EN_LABEL[verdict],
        explanation=explanation,
        confidence_label=f"Confidence: {confidence.value.capitalize()}",
        warnings=list(warnings),
        safety_note=safety_note,
        sources_heading="Sources",
        correction=f"{EN_CORRECTION[verdict]} {first_sentence(explanation)}".strip(),
    )


def first_sentence(text: str) -> str:
    end = min((i for i in (text.find(". "), text.find("\n")) if i >= 0), default=-1)
    return text if end < 0 else text[: end + 1].strip()


async def compose(
    llm: LLMClient,
    *,
    language: str,
    language_name: str,
    verdict: Verdict,
    confidence: Confidence,
    explanation_en: str,
    warnings_en: Sequence[str],
    safety_note_en: str | None,
) -> ComposedReply:
    fallback = english_reply(verdict, confidence, explanation_en, warnings_en, safety_note_en)
    payload = {
        "verdict": EN_LABEL[verdict],
        "confidence": confidence.value,
        "explanation": explanation_en,
        "warnings": list(warnings_en),
        "safety_note": safety_note_en,
    }
    try:
        return await llm.structured(
            stage="compose",
            system=prompts.render("compose.v2"),
            user=prompts.render(
                "compose_task.v1",
                language=language,
                language_name=language_name,
                payload=json.dumps(payload, ensure_ascii=False, indent=1),
            ),
            output=ComposedReply,
            effort="low",
            max_tokens=2000,
        )
    except Exception as exc:  # graceful fallback: answer in English rather than not at all
        log.warning("compose_failed", error=str(exc))
        return fallback


def correction_text(reply: ComposedReply, sources: Sequence[Evidence]) -> str:
    """The forwardable message: the model's short correction plus the best source link."""
    text = reply.correction.strip()[:MAX_CORRECTION_CHARS]
    if not text:
        return ""
    return f"{text}\n{sources[0].url}" if sources else text


def render_text(reply: ComposedReply, verdict: Verdict, sources: Sequence[Evidence]) -> str:
    lines = [f"{EMOJI[verdict]} {reply.verdict_label.upper()}", "", reply.explanation, ""]
    lines.append(reply.confidence_label)
    for w in reply.warnings:
        lines.append(f"⚠️ {w}")
    if reply.safety_note:
        lines += ["", f"🩺 {reply.safety_note}"]
    if sources:
        lines += ["", f"{reply.sources_heading}:"]
        for i, s in enumerate(sources[:MAX_SOURCES], 1):
            who = f" ({s.publisher})" if s.publisher else ""
            lines.append(f"{i}. {s.title[:90]}{who}\n{s.url}")
    correction = correction_text(reply, sources)
    if correction:
        lines += ["", f"📤 {reply.forward_heading}:", correction]
    text = "\n".join(lines).strip()
    return text if len(text) <= TELEGRAM_LIMIT else text[: TELEGRAM_LIMIT - 1] + "…"


def spoken_text(reply: ComposedReply) -> str:
    """What a voice reply says: verdict, explanation, safety note. Links are text-only."""
    parts = [f"{reply.verdict_label}.", reply.explanation]
    if reply.safety_note:
        parts.append(reply.safety_note)
    return " ".join(parts)
