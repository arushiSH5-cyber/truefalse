from app.core.models import (
    ClaimJudgement,
    Confidence,
    Extraction,
    JudgePanelOutput,
    SourceKind,
    Verdict,
)
from app.matching.verdict_cache import (
    InMemoryVerdictCache,
    cache_keys,
    entry_for,
    normalize,
    reuse,
)
from app.pipeline import Deps, run_check
from tests.fakes import FakeLLM, FakeSource


def _panel(verdict: Verdict, ids: list[str], index: int = 0) -> JudgePanelOutput:
    return JudgePanelOutput(
        judgements=[
            ClaimJudgement(
                claim_index=index,
                verdict=verdict,
                confidence=Confidence.HIGH,
                explanation="Studies show lemon water does not cure cancer.",
                cited_evidence_ids=ids,
            )
        ]
    )


def _research_script(llm: FakeLLM) -> None:
    llm.tool_script = [("search_factchecks", {"query": "lemon cures cancer", "language": "es"})]


def test_normalize_ignores_case_punctuation_emoji_and_width() -> None:
    assert normalize("¡El LIMÓN cura el cáncer!!! 🍋") == normalize("el limón  cura el cáncer")
    assert normalize("ＡＢＣ") == "abc"


def test_keys_need_enough_text(spanish_health_extraction: Extraction) -> None:
    claim = spanish_health_extraction.claims[0]
    assert len(cache_keys(claim)) == 2
    short = claim.model_copy(update={"original": "es falso", "english": "it's fake"})
    assert cache_keys(short) == []


def test_unverified_review_and_personal_data_are_not_cached(
    spanish_health_extraction: Extraction, factcheck_draft
) -> None:
    from app.core.models import ClaimDecision, Evidence

    claim = spanish_health_extraction.claims[0]
    ev = Evidence(id="E1", **factcheck_draft.model_dump())
    judgement = _panel(Verdict.FALSE, ["E1"]).judgements[0]
    decision = ClaimDecision(
        judgement=judgement,
        votes=[Verdict.FALSE] * 3,
        agreement=1.0,
        probability=0.9,
        judges_disagree=False,
    )
    assert entry_for(claim, decision, {"E1": ev}) is not None
    assert entry_for(claim, decision, {}) is None  # no cited source
    review = decision.model_copy(update={"needs_human_review": True})
    assert entry_for(claim, review, {"E1": ev}) is None
    unverified = decision.model_copy(
        update={"judgement": judgement.model_copy(update={"verdict": Verdict.UNVERIFIED})}
    )
    assert entry_for(claim, unverified, {"E1": ev}) is None
    phone = claim.model_copy(update={"original": "Llama al +34 612 345 678, cura el cáncer"})
    assert entry_for(phone, decision, {"E1": ev}) is None


def test_reuse_gives_fresh_ids(spanish_health_extraction: Extraction, factcheck_draft) -> None:
    from app.core.models import ClaimDecision, Evidence

    ev = Evidence(id="E7", **factcheck_draft.model_dump())
    decision = ClaimDecision(
        judgement=_panel(Verdict.FALSE, ["E7"]).judgements[0],
        votes=[Verdict.FALSE],
        agreement=1.0,
        probability=0.9,
        judges_disagree=False,
    )
    entry = entry_for(spanish_health_extraction.claims[0], decision, {"E7": ev})
    assert entry is not None
    got, sources = reuse(entry, 2)
    assert got.judgement.claim_index == 2
    assert got.judgement.cited_evidence_ids == ["C2-1"]
    assert [s.id for s in sources] == ["C2-1"]


async def test_repeated_forward_skips_research_and_judges(
    submission, spanish_health_extraction, factcheck_draft
) -> None:
    cache = InMemoryVerdictCache()
    sources = [FakeSource(SourceKind.FACTCHECK, [factcheck_draft])]

    llm = FakeLLM()
    llm.queue("extract", spanish_health_extraction)
    llm.queue_panel(_panel(Verdict.FALSE, ["E1"]))
    _research_script(llm)
    first = await run_check(submission, Deps(llm, sources, verdicts=cache))
    assert first.overall is Verdict.FALSE
    assert first.cached_claims == 0
    assert cache.items  # stored under both keys
    stored = next(iter(cache.items.values()))
    assert "u" * 32 not in stored and submission.id not in stored  # no user or message ids

    # Same claim, written slightly differently, from someone else.
    again = spanish_health_extraction.model_copy(deep=True)
    again.claims[0].original = again.claims[0].original.upper() + "!!"
    llm2 = FakeLLM()
    llm2.queue("extract", again)
    report = await run_check(submission, Deps(llm2, sources, verdicts=cache))

    stages = {c["stage"] for c in llm2.calls}
    assert not any(s.startswith(("research", "verify", "ground")) for s in stages)
    assert report.overall is Verdict.FALSE
    assert report.cached_claims == 1
    assert factcheck_draft.url in report.reply_text
    assert any("We checked this same claim on" in w for w in report.warnings)


async def test_only_new_claims_are_researched(
    submission, spanish_health_extraction, factcheck_draft
) -> None:
    cache = InMemoryVerdictCache()
    sources = [FakeSource(SourceKind.FACTCHECK, [factcheck_draft])]
    llm = FakeLLM()
    llm.queue("extract", spanish_health_extraction)
    llm.queue_panel(_panel(Verdict.FALSE, ["E1"]))
    _research_script(llm)
    await run_check(submission, Deps(llm, sources, verdicts=cache))

    known = spanish_health_extraction.claims[0]
    new = known.model_copy(
        update={
            "original": "El ajo crudo previene la gripe en una semana",
            "english": "Raw garlic prevents the flu within a week",
        }
    )
    two = spanish_health_extraction.model_copy(update={"claims": [new, known]})
    llm2 = FakeLLM()
    llm2.queue("extract", two)
    # Judges only see the new claim, as their claim 0.
    llm2.queue_panel(_panel(Verdict.MISLEADING, ["E1"], index=0))
    _research_script(llm2)
    report = await run_check(submission, Deps(llm2, sources, verdicts=cache))

    verify_call = next(c for c in llm2.calls if c["stage"].startswith("verify."))
    assert "garlic" in verify_call["user"] and "Lemon" not in verify_call["user"]
    assert [j.claim_index for j in report.judgements] == [0, 1]
    assert [j.verdict for j in report.judgements] == [Verdict.MISLEADING, Verdict.FALSE]
    assert report.cached_claims == 1
    ids = {e.id for e in report.evidence}
    assert all(i in ids for j in report.judgements for i in j.cited_evidence_ids)


async def test_cache_outage_falls_back_to_full_check(
    submission, spanish_health_extraction, factcheck_draft
) -> None:
    class Broken(InMemoryVerdictCache):
        async def get(self, claim):  # type: ignore[no-untyped-def]
            raise ConnectionError("redis down")

        async def put(self, claim, entry):  # type: ignore[no-untyped-def]
            raise ConnectionError("redis down")

    llm = FakeLLM()
    llm.queue("extract", spanish_health_extraction)
    llm.queue_panel(_panel(Verdict.FALSE, ["E1"]))
    _research_script(llm)
    sources = [FakeSource(SourceKind.FACTCHECK, [factcheck_draft])]
    report = await run_check(submission, Deps(llm, sources, verdicts=Broken()))
    assert report.overall is Verdict.FALSE
