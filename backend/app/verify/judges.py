"""Verification: a panel of independent judges, combined into a calibrated decision.

Each judge sees the same claims + evidence through a different lens (prompt) and must cite
evidence ids. The core rule is applied to every vote and again to the final decision.
Disagreement between judges, or weak evidence, ends in "Unverified".
"""

import asyncio
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from app.core import prompts
from app.core.llm import Effort, LLMClient
from app.core.logging import get_logger
from app.core.models import (
    Claim,
    ClaimDecision,
    ClaimJudgement,
    Confidence,
    Evidence,
    JudgePanelOutput,
    Verdict,
)
from app.verify.calibration import (
    CONFIDENCE_VALUE,
    MIN_VERDICT,
    Calibrator,
    Features,
    confidence_for,
)
from app.verify.ratings import polarity, rating_verdict, resolve_ratings

log = get_logger(__name__)

# Most to least severe. A message containing one false claim is "False" overall.
SEVERITY = [Verdict.FALSE, Verdict.MISLEADING, Verdict.UNVERIFIED, Verdict.TRUE]

NO_SOURCE_EXPLANATION = (
    "We could not find a trustworthy source that checks this claim, "
    "so we cannot say if it is true or false."
)
DISAGREE_EXPLANATION = (
    "The evidence we found points in different directions, so we cannot give a clear answer."
)


@dataclass(frozen=True)
class JudgeSpec:
    name: str
    prompt: str
    effort: Effort


PANEL = (
    JudgeSpec("balanced", "judge.v1", "high"),
    JudgeSpec("skeptic", "judge_skeptic.v1", "high"),
    JudgeSpec("context", "judge_context.v1", "medium"),
)
MIN_VALID_JUDGES = 2


def _unverified(index: int, explanation: str = NO_SOURCE_EXPLANATION) -> ClaimJudgement:
    return ClaimJudgement(
        claim_index=index,
        verdict=Verdict.UNVERIFIED,
        confidence=Confidence.LOW,
        explanation=explanation,
        cited_evidence_ids=[],
    )


def enforce_core_rule(judgement: ClaimJudgement, evidence: dict[str, Evidence]) -> ClaimJudgement:
    """Never give a verdict without at least one cited, retrieved source."""
    cited = [eid for eid in judgement.cited_evidence_ids if eid in evidence]
    if judgement.verdict is Verdict.UNVERIFIED:
        return judgement.model_copy(
            update={
                "cited_evidence_ids": cited,
                "explanation": judgement.explanation or NO_SOURCE_EXPLANATION,
            }
        )
    if not cited:
        return _unverified(judgement.claim_index)
    # Low-tier-only support can never be high confidence.
    if judgement.confidence is Confidence.HIGH and min(evidence[e].tier for e in cited) == 3:
        return judgement.model_copy(
            update={"confidence": Confidence.MEDIUM, "cited_evidence_ids": cited}
        )
    return judgement.model_copy(update={"cited_evidence_ids": cited})


def overall_verdict(judgements: Sequence[ClaimJudgement]) -> tuple[Verdict, Confidence]:
    if not judgements:
        return Verdict.UNVERIFIED, Confidence.LOW
    worst = min(judgements, key=lambda j: SEVERITY.index(j.verdict))
    return worst.verdict, worst.confidence


def factcheck_consensus(verdict: Verdict, cited: Sequence[Evidence]) -> int:
    """+1 if cited fact-check ratings agree with the verdict, -1 if any contradict, 0 if none."""
    target = polarity(verdict)
    signs = [polarity(rating_verdict(e)) for e in cited if e.rating]
    signs = [s for s in signs if s != 0]
    if not signs or target == 0:
        return 0
    return -1 if any(s != target for s in signs) else 1


def evidence_conflict(cited: Sequence[Evidence]) -> bool:
    """True when fact-check ratings among the evidence point in opposite directions."""
    signs = {polarity(rating_verdict(e)) for e in cited if e.rating} - {0}
    return len(signs) > 1


def decide(
    index: int,
    votes: Sequence[ClaimJudgement],
    evidence: dict[str, Evidence],
    calibrator: Calibrator,
) -> ClaimDecision:
    """Combine one claim's (core-rule-checked) votes into a final decision."""
    verdicts = [v.verdict for v in votes]

    def result(
        judgement: ClaimJudgement, p: float, agreement: float, disagree: bool
    ) -> ClaimDecision:
        return ClaimDecision(
            judgement=judgement,
            votes=verdicts,
            agreement=round(agreement, 3),
            probability=round(p, 3),
            judges_disagree=disagree,
        )

    if len(votes) < MIN_VALID_JUDGES:
        return result(_unverified(index), 0.0, 0.0, False)

    counts = Counter(verdicts)
    majority, n = counts.most_common(1)[0]
    negative = [v for v in votes if v.verdict in (Verdict.FALSE, Verdict.MISLEADING)]
    if n < 2 and len(negative) >= 2:
        # Judges agree the claim is wrong but not how wrong: take the milder verdict.
        majority, n = Verdict.MISLEADING, len(negative)
    agreement = n / len(votes)
    opposite = Verdict.TRUE in counts and bool(negative)
    if n < 2 or opposite:
        return result(_unverified(index, DISAGREE_EXPLANATION), 0.0, agreement, True)
    if majority is Verdict.UNVERIFIED:
        return result(_unverified(index), 0.0, agreement, False)

    agreeing = (
        [v for v in votes if v.verdict is majority]
        if majority is not Verdict.MISLEADING or counts[Verdict.MISLEADING] >= 2
        else negative
    )
    cited_ids = list(dict.fromkeys(e for v in agreeing for e in v.cited_evidence_ids))
    cited = [evidence[e] for e in cited_ids]
    features = Features(
        agreement=agreement,
        best_tier=min(e.tier for e in cited),
        n_sources=len(cited),
        factcheck_consensus=factcheck_consensus(majority, cited),
        judge_confidence=sum(CONFIDENCE_VALUE[v.confidence] for v in agreeing) / len(agreeing),
    )
    p = calibrator.predict(features)
    if p < MIN_VERDICT or features.factcheck_consensus < 0:
        explanation = DISAGREE_EXPLANATION if features.factcheck_consensus < 0 else ""
        return result(
            _unverified(index, explanation or NO_SOURCE_EXPLANATION),
            p,
            agreement,
            features.factcheck_consensus < 0,
        )

    # Explanation from the most confident agreeing judge (panel order breaks ties).
    best = max(agreeing, key=lambda v: CONFIDENCE_VALUE[v.confidence])
    final = best.model_copy(
        update={
            "verdict": majority,
            "confidence": confidence_for(p),
            "cited_evidence_ids": cited_ids,
        }
    )
    return result(enforce_core_rule(final, evidence), p, agreement, False)


def _evidence_block(evidence: Sequence[Evidence]) -> str:
    return json.dumps(
        [
            {
                "id": e.id,
                "tier": e.tier,
                "source": e.source.value,
                "title": e.title,
                "publisher": e.publisher,
                "date": e.published,
                "fact_checker_rating": e.rating,
                "quote": e.quote,
            }
            for e in evidence
        ],
        ensure_ascii=False,
        indent=1,
    )


async def _one_judge(
    llm: LLMClient, spec: JudgeSpec, claims_text: str, evidence_text: str
) -> list[ClaimJudgement] | None:
    try:
        out = await llm.structured(
            stage=f"verify.{spec.name}",
            system=prompts.render(spec.prompt),
            user=prompts.render("judge_task.v1", claims=claims_text, evidence=evidence_text),
            output=JudgePanelOutput,
            effort=spec.effort,
        )
        return out.judgements
    except Exception as exc:  # one judge failing must not sink the panel
        log.warning("judge_failed", judge=spec.name, error=str(exc))
        return None


async def judge(
    llm: LLMClient,
    claims: Sequence[Claim],
    evidence: Sequence[Evidence],
    *,
    panel: Sequence[JudgeSpec] = PANEL,
    calibrator: Calibrator | None = None,
) -> list[ClaimDecision]:
    calibrator = calibrator or Calibrator.load()
    if not evidence:
        return [
            ClaimDecision(
                judgement=_unverified(i),
                votes=[],
                agreement=0.0,
                probability=0.0,
                judges_disagree=False,
            )
            for i in range(len(claims))
        ]

    evidence = await resolve_ratings(llm, evidence)
    by_id = {e.id: e for e in evidence}
    claims_text = "\n".join(f"{i}. [{c.topic}] {c.english}" for i, c in enumerate(claims))
    evidence_text = _evidence_block(evidence)
    outputs = await asyncio.gather(
        *(_one_judge(llm, spec, claims_text, evidence_text) for spec in panel)
    )

    decisions: list[ClaimDecision] = []
    for i in range(len(claims)):
        votes: list[ClaimJudgement] = []
        for out in outputs:
            if out is None:
                continue
            mine = next((j for j in out if j.claim_index == i), None)
            if mine is not None:
                votes.append(enforce_core_rule(mine, by_id))
        decisions.append(decide(i, votes, by_id, calibrator))
    return decisions
