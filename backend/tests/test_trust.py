"""Phase 4 trust layer: judge panel, calibration, ratings, grounding, disagreement."""

import pytest

from app.core.llm import LLMError
from app.core.models import (
    Claim,
    ClaimDecision,
    ClaimJudgement,
    Confidence,
    Evidence,
    GroundedExplanation,
    GroundedSentence,
    GroundingOutput,
    JudgePanelOutput,
    SourceKind,
    Verdict,
)
from app.safety.grounding_check import ground, numbers_supported, split_sentences
from app.verify.calibration import Calibrator, Features, confidence_for, expected_calibration_error
from app.verify.judges import DISAGREE_EXPLANATION, decide, evidence_conflict, judge
from app.verify.ratings import normalize_rating
from tests.fakes import FakeLLM

V = Verdict
C = Confidence


def _ev(
    eid: str, tier: int = 1, rating: str | None = None, quote: str = "q", publisher: str = "WHO"
) -> Evidence:
    return Evidence(
        id=eid,
        source=SourceKind.WEB,
        tier=tier,
        title=f"title {eid}",
        url=f"https://x/{eid}",
        quote=quote,
        rating=rating,
        publisher=publisher,
    )


EVIDENCE = {"E1": _ev("E1", 1), "E2": _ev("E2", 2, rating="False"), "E3": _ev("E3", 3)}
CLAIM = Claim(original="o", english="Lemon cures cancer", topic="health", potentially_harmful=True)


def _vote(verdict: Verdict, conf: Confidence = C.HIGH, cited=("E1",), expl="Evidence says no."):
    return ClaimJudgement(
        claim_index=0,
        verdict=verdict,
        confidence=conf,
        explanation=expl,
        cited_evidence_ids=list(cited),
    )


# ---- ratings -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rating", "expected"),
    [
        ("False", V.FALSE),
        ("Pants on Fire!", V.FALSE),
        ("Falso", V.FALSE),
        ("Faux", V.FALSE),
        ("झूठ", V.FALSE),
        ("غير صحيح", V.FALSE),
        ("虚假", V.FALSE),
        ("Hoaks", V.FALSE),
        ("Mostly False", V.MISLEADING),
        ("Missing context", V.MISLEADING),
        ("Engañoso", V.MISLEADING),
        ("Trompeur", V.MISLEADING),
        ("Half True", V.MISLEADING),
        ("True", V.TRUE),
        ("Verdadero", V.TRUE),
        ("صحيح", V.TRUE),
        ("Correct", V.TRUE),
        ("Unproven", V.UNVERIFIED),
        ("Inexact", V.MISLEADING),
        ("Not real", V.FALSE),
        (None, None),
        ("Four Pinocchios", None),
    ],
)
def test_normalize_rating(rating, expected) -> None:
    assert normalize_rating(rating) is expected


def test_evidence_conflict() -> None:
    assert evidence_conflict([_ev("a", rating="False"), _ev("b", rating="True")])
    assert not evidence_conflict([_ev("a", rating="False"), _ev("b", rating="Mostly false")])


# ---- calibration ---------------------------------------------------------------------------


def test_calibrator_orders_strong_above_weak() -> None:
    cal = Calibrator()
    strong = cal.predict(Features(1.0, 1, 3, 1, 1.0))
    weak = cal.predict(Features(2 / 3, 3, 1, 0, 1 / 3))
    assert strong > 0.9 and weak < 0.5
    assert confidence_for(strong) is C.HIGH and confidence_for(0.7) is C.MEDIUM


def test_calibrator_fit_and_save(tmp_path) -> None:
    good, bad = Features(1.0, 1, 3, 1, 1.0), Features(2 / 3, 3, 1, -1, 1 / 3)
    cal = Calibrator(weights=[0.0] * 6, bias=0.0).fit([good, bad] * 20, [1, 0] * 20)
    assert cal.predict(good) > 0.8 > 0.2 > cal.predict(bad)
    cal.save(tmp_path / "p.json")
    assert Calibrator.load(tmp_path / "p.json").weights == cal.weights


def test_ece() -> None:
    assert expected_calibration_error([1.0, 1.0, 0.0], [1, 1, 0]) == pytest.approx(0.0)
    assert expected_calibration_error([0.9] * 10, [0] * 10) == pytest.approx(0.9)


# ---- panel decisions -----------------------------------------------------------------------


def test_unanimous_strong_verdict() -> None:
    d = decide(0, [_vote(V.FALSE, cited=("E1", "E2"))] * 3, EVIDENCE, Calibrator())
    assert d.judgement.verdict is V.FALSE and d.judgement.confidence is C.HIGH
    assert d.agreement == 1.0 and d.probability > 0.85 and not d.judges_disagree


def test_opposite_votes_mean_unverified() -> None:
    d = decide(0, [_vote(V.FALSE), _vote(V.FALSE), _vote(V.TRUE)], EVIDENCE, Calibrator())
    assert d.judgement.verdict is V.UNVERIFIED and d.judges_disagree
    assert d.judgement.explanation == DISAGREE_EXPLANATION


def test_false_vs_misleading_takes_milder() -> None:
    d = decide(
        0,
        [
            _vote(V.FALSE, cited=("E1", "E2")),
            _vote(V.MISLEADING, cited=("E1", "E2")),
            _vote(V.UNVERIFIED, cited=()),
        ],
        EVIDENCE,
        Calibrator(),
    )
    assert d.judgement.verdict is V.MISLEADING


def test_too_few_judges_or_weak_evidence() -> None:
    assert decide(0, [_vote(V.FALSE)], EVIDENCE, Calibrator()).judgement.verdict is V.UNVERIFIED
    weak = [
        _vote(V.TRUE, C.LOW, cited=("E3",)),
        _vote(V.TRUE, C.LOW, cited=("E3",)),
        _vote(V.UNVERIFIED, cited=()),
    ]
    d = decide(0, weak, EVIDENCE, Calibrator())
    assert d.judgement.verdict is V.UNVERIFIED and d.probability < 0.5


def test_factcheck_contradicting_verdict_is_unverified() -> None:
    # Judges say TRUE but the cited fact-check rates the claim False.
    d = decide(0, [_vote(V.TRUE, cited=("E1", "E2"))] * 3, EVIDENCE, Calibrator())
    assert d.judgement.verdict is V.UNVERIFIED and d.judges_disagree


async def test_panel_survives_one_failed_judge() -> None:
    llm = FakeLLM().queue_panel(
        JudgePanelOutput(judgements=[_vote(V.FALSE, cited=("E1", "E2"))]),
        LLMError("judge down"),
        JudgePanelOutput(judgements=[_vote(V.FALSE, cited=("E1",))]),
    )
    (d,) = await judge(llm, [CLAIM], list(EVIDENCE.values()))
    assert d.judgement.verdict is V.FALSE and d.votes == [V.FALSE, V.FALSE]
    assert d.judgement.cited_evidence_ids == ["E1", "E2"]


async def test_panel_judges_use_different_prompts() -> None:
    llm = FakeLLM().queue_panel(JudgePanelOutput(judgements=[_vote(V.FALSE)]))
    await judge(llm, [CLAIM], list(EVIDENCE.values()))
    systems = {c["system"] for c in llm.calls}
    assert len(systems) == 3


# ---- grounding -----------------------------------------------------------------------------


def _decision(expl: str, cited=("E1",)) -> ClaimDecision:
    return ClaimDecision(
        judgement=_vote(V.FALSE, cited=cited, expl=expl),
        votes=[V.FALSE] * 3,
        agreement=1.0,
        probability=0.95,
        judges_disagree=False,
    )


def test_number_check() -> None:
    cited = [_ev("E1", quote="A 2019 trial of 120 patients found no benefit.")]
    assert numbers_supported("A trial of 120 patients found no benefit.", cited)
    assert not numbers_supported("It kills 95% of cancer cells.", cited)
    assert split_sentences("One. Two! 三。四") == ["One.", "Two!", "三。", "四"]


async def test_grounding_drops_unsupported_sentences() -> None:
    llm = FakeLLM().queue(
        "ground",
        GroundingOutput(
            explanations=[
                GroundedExplanation(
                    claim_index=0,
                    sentences=[
                        GroundedSentence(index=0, supported=True, evidence_ids=["E1"]),
                        GroundedSentence(index=1, supported=False, evidence_ids=[]),
                    ],
                )
            ]
        ),
    )
    (d,) = await ground(
        llm,
        [
            _decision(
                "Lemon does not cure cancer. Doctors hate this trick. It works for 95% of people."
            )
        ],
        EVIDENCE,
    )
    assert d.judgement.explanation == "Lemon does not cure cancer."
    assert d.grounded_ratio == pytest.approx(1 / 3, abs=0.01)


async def test_grounding_failure_uses_template() -> None:
    llm = FakeLLM().queue("ground", LLMError("down"))
    (d,) = await ground(llm, [_decision("Something.", cited=("E1", "E2"))], EVIDENCE)
    assert d.judgement.explanation.startswith("Based on WHO, this claim is false")
    assert d.grounded_ratio == 0.0


async def test_grounding_skips_unverified() -> None:
    llm = FakeLLM()
    unv = ClaimDecision(
        judgement=_vote(V.UNVERIFIED, cited=()),
        votes=[],
        agreement=0,
        probability=0,
        judges_disagree=False,
    )
    assert await ground(llm, [unv], EVIDENCE) == [unv]
    assert llm.calls == []
