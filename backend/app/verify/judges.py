"""Verification. Phase 2: a single judge + the core rule. Phase 4 adds the 3-judge panel."""

import json
from collections.abc import Sequence

from app.core import prompts
from app.core.llm import LLMClient
from app.core.models import (
    Claim,
    ClaimJudgement,
    Confidence,
    Evidence,
    JudgePanelOutput,
    Verdict,
)

# Most to least severe. A message containing one false claim is "False" overall.
SEVERITY = [Verdict.FALSE, Verdict.MISLEADING, Verdict.UNVERIFIED, Verdict.TRUE]

NO_SOURCE_EXPLANATION = (
    "We could not find a trustworthy source that checks this claim, "
    "so we cannot say if it is true or false."
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
        return judgement.model_copy(
            update={
                "verdict": Verdict.UNVERIFIED,
                "confidence": Confidence.LOW,
                "cited_evidence_ids": [],
                "explanation": NO_SOURCE_EXPLANATION,
            }
        )
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


async def judge(
    llm: LLMClient, claims: Sequence[Claim], evidence: Sequence[Evidence]
) -> list[ClaimJudgement]:
    by_id = {e.id: e for e in evidence}
    if not evidence:
        raw = [
            ClaimJudgement(
                claim_index=i,
                verdict=Verdict.UNVERIFIED,
                confidence=Confidence.LOW,
                explanation="",
                cited_evidence_ids=[],
            )
            for i in range(len(claims))
        ]
    else:
        out = await llm.structured(
            stage="verify",
            system=prompts.render("judge.v1"),
            user=prompts.render(
                "judge_task.v1",
                claims="\n".join(f"{i}. [{c.topic}] {c.english}" for i, c in enumerate(claims)),
                evidence=_evidence_block(evidence),
            ),
            output=JudgePanelOutput,
            effort="high",
        )
        raw = out.judgements

    # Exactly one judgement per claim; missing ones become Unverified.
    by_claim = {j.claim_index: j for j in raw if 0 <= j.claim_index < len(claims)}
    result: list[ClaimJudgement] = []
    for i in range(len(claims)):
        j = by_claim.get(i) or ClaimJudgement(
            claim_index=i,
            verdict=Verdict.UNVERIFIED,
            confidence=Confidence.LOW,
            explanation="",
            cited_evidence_ids=[],
        )
        result.append(enforce_core_rule(j, by_id))
    return result
