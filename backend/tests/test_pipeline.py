from app.core.llm import LLMError
from app.core.models import (
    ClaimJudgement,
    ComposedReply,
    Confidence,
    Extraction,
    JudgePanelOutput,
    SourceKind,
    Verdict,
)
from app.core.trace import RunTrace
from app.pipeline import NO_CLAIM_EXPLANATION, Deps, PipelineConfig, run_check
from tests.fakes import FakeLLM, FakeSource


def _composed_es() -> ComposedReply:
    return ComposedReply(
        verdict_label="Falso",
        explanation="El agua con limón no cura el cáncer.",
        confidence_label="Confianza: Alta",
        warnings=[],
        safety_note="No somos médicos. Habla con un médico.",
        sources_heading="Fuentes",
    )


async def test_false_health_claim_full_flow(
    submission, spanish_health_extraction, factcheck_draft, pubmed_draft
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
                    explanation="Doctors and studies say lemon water does not cure cancer.",
                    cited_evidence_ids=["E1", "E2"],
                )
            ]
        ),
    )
    llm.queue("compose", _composed_es())
    llm.tool_script = [
        ("search_factchecks", {"query": "lemon cures cancer", "language": "es"}),
        ("search_pubmed", {"query": "citrus cancer treatment", "language": None}),
    ]
    sources = [
        FakeSource(SourceKind.FACTCHECK, [factcheck_draft]),
        FakeSource(SourceKind.PUBMED, [pubmed_draft]),
    ]
    trace = RunTrace()

    report = await run_check(submission, Deps(llm, sources), trace)

    assert report.overall is Verdict.FALSE
    assert report.language == "es"
    assert report.reply_text.startswith("🔴 FALSO")
    # Tier-1 source is listed first; URLs come from evidence, not the model.
    assert report.reply_text.index(pubmed_draft.url) < report.reply_text.index(factcheck_draft.url)
    assert "🩺" in report.reply_text
    compose_call = next(c for c in llm.calls if c["stage"] == "compose")
    assert "Target language: es (Spanish)" in compose_call["user"]
    assert "not doctors" in compose_call["user"]  # health note passed for translation
    assert "could harm" in compose_call["user"]  # danger note for harmful false claim
    assert set(trace.stages) == {
        "modality",
        "extract",
        "match",
        "research",
        "verify",
        "verify.balanced",
        "verify.skeptic",
        "verify.context",
        "ground",
        "compose",
    }


async def test_no_evidence_means_unverified_without_judge(submission, spanish_health_extraction):
    llm = FakeLLM().queue("extract", spanish_health_extraction)
    llm.queue("compose", LLMError("down"))  # also exercises the English fallback
    report = await run_check(submission, Deps(llm, [FakeSource(SourceKind.WEB, [])]))
    assert report.overall is Verdict.UNVERIFIED
    assert report.overall_confidence is Confidence.LOW
    assert not any(c["stage"].startswith("verify") for c in llm.calls)
    assert report.reply_text.startswith("⚪ UNVERIFIED")


async def test_judge_citing_unknown_evidence_is_downgraded(
    submission, spanish_health_extraction, factcheck_draft
):
    llm = FakeLLM().queue("extract", spanish_health_extraction)
    llm.queue_panel(
        JudgePanelOutput(
            judgements=[
                ClaimJudgement(
                    claim_index=0,
                    verdict=Verdict.FALSE,
                    confidence=Confidence.HIGH,
                    explanation="x",
                    cited_evidence_ids=["E99"],
                )
            ]
        ),
    )
    llm.queue("compose", _composed_es())
    llm.tool_script = [("search_factchecks", {"query": "q", "language": None})]
    report = await run_check(
        submission, Deps(llm, [FakeSource(SourceKind.FACTCHECK, [factcheck_draft])])
    )
    assert report.overall is Verdict.UNVERIFIED
    assert factcheck_draft.url not in report.reply_text


async def test_no_claims_skips_research(submission):
    llm = FakeLLM().queue(
        "extract",
        Extraction(
            language="en",
            language_name="English",
            script="Latin",
            romanized_or_mixed=False,
            looks_like_satire_or_opinion=False,
            claims=[],
        ),
    )
    llm.queue("compose", LLMError("down"))
    source = FakeSource(SourceKind.WEB)
    report = await run_check(submission, Deps(llm, [source]))
    assert source.queries == []
    assert NO_CLAIM_EXPLANATION in report.reply_text


async def test_failing_source_does_not_crash(submission, spanish_health_extraction):
    llm = FakeLLM().queue("extract", spanish_health_extraction)
    llm.queue("compose", _composed_es())
    llm.tool_script = [("search_web", {"query": "q", "language": None})]
    report = await run_check(
        submission,
        Deps(llm, [FakeSource(SourceKind.WEB, error=RuntimeError("boom"))]),
    )
    assert report.overall is Verdict.UNVERIFIED


async def test_tool_call_cap(submission, spanish_health_extraction, factcheck_draft):
    llm = FakeLLM().queue("extract", spanish_health_extraction)
    llm.queue_panel(JudgePanelOutput(judgements=[]))
    llm.queue("compose", _composed_es())
    llm.tool_script = [
        ("search_factchecks", {"query": f"q{i}", "language": None}) for i in range(10)
    ]
    source = FakeSource(SourceKind.FACTCHECK, [factcheck_draft])
    await run_check(submission, Deps(llm, [source], PipelineConfig(research_max_tool_calls=3)))
    # 2 code-guaranteed fact-check lookups (English, then Spanish original) + 3 capped agent calls
    assert source.queries[:2] == [
        ("Hot water with lemon cures cancer", None),
        ("El agua caliente con limón cura el cáncer", "es"),
    ]
    assert [q for q, _ in source.queries[2:]] == ["q0", "q1", "q2"]
