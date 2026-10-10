from app.core.models import ClaimJudgement, Confidence, Evidence, SourceKind, Verdict
from app.verify.judges import enforce_core_rule, overall_verdict


def _ev(eid: str, tier: int) -> Evidence:
    return Evidence(
        id=eid, source=SourceKind.WEB, tier=tier, title="t", url=f"https://x/{eid}", quote="q"
    )


def _j(verdict: Verdict, conf: Confidence = Confidence.HIGH, cited=("E1",), idx=0):
    return ClaimJudgement(
        claim_index=idx,
        verdict=verdict,
        confidence=conf,
        explanation="e",
        cited_evidence_ids=list(cited),
    )


def test_verdict_without_citation_becomes_unverified() -> None:
    out = enforce_core_rule(_j(Verdict.TRUE, cited=()), {"E1": _ev("E1", 1)})
    assert out.verdict is Verdict.UNVERIFIED and out.confidence is Confidence.LOW


def test_hallucinated_ids_are_dropped() -> None:
    out = enforce_core_rule(_j(Verdict.FALSE, cited=("E1", "E7")), {"E1": _ev("E1", 1)})
    assert out.verdict is Verdict.FALSE and out.cited_evidence_ids == ["E1"]


def test_tier3_only_caps_confidence() -> None:
    out = enforce_core_rule(_j(Verdict.FALSE), {"E1": _ev("E1", 3)})
    assert out.confidence is Confidence.MEDIUM


def test_overall_is_most_severe() -> None:
    js = [
        _j(Verdict.TRUE, idx=0),
        _j(Verdict.FALSE, Confidence.MEDIUM, idx=1),
        _j(Verdict.UNVERIFIED, idx=2),
    ]
    assert overall_verdict(js) == (Verdict.FALSE, Confidence.MEDIUM)
    assert (
        overall_verdict([_j(Verdict.TRUE), _j(Verdict.UNVERIFIED, Confidence.LOW, idx=1)])[0]
        is Verdict.UNVERIFIED
    )
    assert overall_verdict([]) == (Verdict.UNVERIFIED, Confidence.LOW)
