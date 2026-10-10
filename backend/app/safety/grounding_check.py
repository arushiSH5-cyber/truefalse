"""Grounding check: every factual sentence in an explanation must be backed by cited evidence.

Two layers:
1. Deterministic: a sentence containing a number that appears in none of the cited evidence
   is dropped (invented statistics are the most common hallucination).
2. LLM: each sentence is checked against the cited evidence; unsupported sentences are dropped.

If nothing survives, a template explanation built only from evidence metadata is used.
"""

import json
import re
from collections.abc import Sequence

from app.core import prompts
from app.core.llm import LLMClient
from app.core.logging import get_logger
from app.core.models import ClaimDecision, Evidence, GroundingOutput, Verdict

log = get_logger(__name__)

# Latin-style punctuation needs a following space; CJK, Devanagari danda and Arabic do not.
_SENTENCE = re.compile(r"(?<=[.!?])\s+|(?<=[。！？।؟])\s*")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")

VERDICT_WORDS = {
    Verdict.TRUE: "true",
    Verdict.MISLEADING: "misleading",
    Verdict.FALSE: "false",
    Verdict.UNVERIFIED: "unverified",
}


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text.strip()) if s.strip()]


def numbers_supported(sentence: str, cited: Sequence[Evidence]) -> bool:
    haystack = " ".join(f"{e.title} {e.quote} {e.published or ''}" for e in cited)
    return all(n in haystack for n in _NUMBER.findall(sentence))


def template_explanation(verdict: Verdict, cited: Sequence[Evidence]) -> str:
    names = list(dict.fromkeys(e.publisher for e in cited if e.publisher))[:2]
    who = " and ".join(names) if names else "the trusted sources we found"
    return f"Based on {who}, this claim is {VERDICT_WORDS[verdict]}. See the sources below."


async def ground(
    llm: LLMClient, decisions: Sequence[ClaimDecision], evidence: dict[str, Evidence]
) -> list[ClaimDecision]:
    """Return decisions whose explanations contain only supported sentences."""
    todo = [d for d in decisions if d.judgement.verdict is not Verdict.UNVERIFIED]
    if not todo:
        return list(decisions)

    pre: dict[int, list[str]] = {}
    for d in todo:
        j = d.judgement
        cited = [evidence[e] for e in j.cited_evidence_ids if e in evidence]
        pre[j.claim_index] = [
            s for s in split_sentences(j.explanation) if numbers_supported(s, cited)
        ]

    payload = [
        {
            "claim_index": d.judgement.claim_index,
            "verdict": d.judgement.verdict.value,
            "sentences": dict(enumerate(pre[d.judgement.claim_index])),
            "evidence": [
                {
                    "id": e,
                    "title": evidence[e].title,
                    "quote": evidence[e].quote,
                    "rating": evidence[e].rating,
                }
                for e in d.judgement.cited_evidence_ids
                if e in evidence
            ],
        }
        for d in todo
    ]
    supported: dict[int, list[str]] = {}
    try:
        out = await llm.structured(
            stage="ground",
            system=prompts.render("grounding.v1"),
            user=json.dumps(payload, ensure_ascii=False, indent=1),
            output=GroundingOutput,
            effort="low",
        )
        for exp in out.explanations:
            sentences = pre.get(exp.claim_index, [])
            ok = {
                s.index
                for s in exp.sentences
                if s.supported and s.evidence_ids and 0 <= s.index < len(sentences)
            }
            supported[exp.claim_index] = [sentences[i] for i in sorted(ok)]
    except Exception as exc:
        # Can't verify grounding -> fall back to the template for every explanation.
        log.warning("grounding_failed", error=str(exc))

    result: list[ClaimDecision] = []
    for d in decisions:
        j = d.judgement
        if j.verdict is Verdict.UNVERIFIED:
            result.append(d)
            continue
        total = len(split_sentences(j.explanation)) or 1
        kept = supported.get(j.claim_index, [])
        cited = [evidence[e] for e in j.cited_evidence_ids if e in evidence]
        text = " ".join(kept) if kept else template_explanation(j.verdict, cited)
        result.append(
            d.model_copy(
                update={
                    "judgement": j.model_copy(update={"explanation": text}),
                    "grounded_ratio": round(len(kept) / total, 3),
                }
            )
        )
    return result
