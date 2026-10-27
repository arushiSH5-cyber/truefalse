"""Reply card: verdict, two-line reason, at most 3 sources, forwardable correction message."""

from app.core.jobs import public_result
from app.core.models import (
    ClaimJudgement,
    ComposedReply,
    Confidence,
    Evidence,
    JudgePanelOutput,
    SourceKind,
    Verdict,
)
from app.core.prompts import render
from app.pipeline import Deps, run_check
from app.respond.compose import MAX_SOURCES, correction_text, english_reply, render_text
from tests.fakes import FakeLLM, FakeSource


def _evidence(n: int) -> list[Evidence]:
    return [
        Evidence(
            id=f"E{i}",
            source=SourceKind.WEB,
            tier=1,
            title=f"Source {i}",
            url=f"https://example.org/{i}",
            quote="q",
        )
        for i in range(1, n + 1)
    ]


def _reply(**kw) -> ComposedReply:
    base = dict(
        verdict_label="Falso",
        explanation="El agua con limón no cura el cáncer. Los médicos lo confirman.",
        confidence_label="Confianza: Alta",
        warnings=[],
        safety_note=None,
        sources_heading="Fuentes",
        correction="Lo verifiqué: el agua con limón no cura el cáncer.",
        forward_heading="Reenvía esto",
    )
    return ComposedReply(**(base | kw))


def test_reply_has_three_sources_and_correction_with_top_link() -> None:
    text = render_text(_reply(), Verdict.FALSE, _evidence(5))
    assert "https://example.org/3" in text and "https://example.org/4" not in text
    assert MAX_SOURCES == 3
    tail = text.split("📤 Reenvía esto:\n", 1)[1]
    assert tail == "Lo verifiqué: el agua con limón no cura el cáncer.\nhttps://example.org/1"


def test_no_correction_section_when_empty() -> None:
    text = render_text(_reply(correction=""), Verdict.FALSE, _evidence(1))
    assert "📤" not in text
    assert correction_text(_reply(), []) == "Lo verifiqué: el agua con limón no cura el cáncer."


def test_english_fallback_still_has_a_correction() -> None:
    r = english_reply(
        Verdict.UNVERIFIED,
        Confidence.LOW,
        "No reliable source confirms this. It may be a rumor.",
        [],
        None,
    )
    assert r.correction == (
        "I checked this message, but it could not be confirmed. Please wait before sharing it. "
        "No reliable source confirms this."
    )


def test_compose_prompt_asks_for_two_lines_and_correction() -> None:
    system = render("compose.v2")
    assert "two-line reason" in system and "`correction`" in system
    assert "without blaming" in system


async def test_web_result_uses_translated_text(
    submission, spanish_health_extraction, factcheck_draft
) -> None:
    llm = FakeLLM()
    llm.queue("extract", spanish_health_extraction)
    llm.queue_panel(
        JudgePanelOutput(
            judgements=[
                ClaimJudgement(
                    claim_index=0,
                    verdict=Verdict.FALSE,
                    confidence=Confidence.HIGH,
                    explanation="Lemon water does not cure cancer.",
                    cited_evidence_ids=["E1"],
                )
            ]
        )
    )
    llm.queue("compose", _reply())
    llm.tool_script = [("search_factchecks", {"query": "lemon cancer", "language": "es"})]
    report = await run_check(
        submission, Deps(llm, [FakeSource(SourceKind.FACTCHECK, [factcheck_draft])])
    )
    result = public_result(report)
    assert result.explanation.startswith("El agua con limón")  # not the English judge text
    assert result.verdict_label == "Falso"
    assert result.confidence_label == "Confianza: Alta"
    assert result.forward_heading == "Reenvía esto"
    assert result.correction.endswith(factcheck_draft.url)
    assert "📤 Reenvía esto:" in report.reply_text
