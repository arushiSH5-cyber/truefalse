"""End-to-end check: extract -> research -> verify -> safety -> respond."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.claims.extract import extract_claims
from app.core.llm import LLMClient
from app.core.logging import get_logger
from app.core.models import (
    CheckReport,
    Claim,
    ClaimDecision,
    ClaimJudgement,
    Confidence,
    Evidence,
    EvidenceDraft,
    Extraction,
    PartKind,
    Submission,
    Verdict,
)
from app.core.trace import RunTrace, current_trace
from app.language.accuracy import LOWER_ACCURACY_WARNING, is_lower_accuracy
from app.language.detect import LanguageDetector
from app.language.names import english_name
from app.language.scripts import is_mixed_script, looks_romanized
from app.matching.factcheck_index import FactCheckIndex
from app.modality.media import temp_workdir
from app.modality.process import (
    MediaProviders,
    ProcessedContent,
    ProgressFn,
    process_submission,
)
from app.research.agent import research
from app.research.sources.base import Source
from app.respond.compose import compose, render_text, spoken_text
from app.safety import topic_rules
from app.safety.grounding_check import ground
from app.verify.judges import PANEL, JudgeSpec, overall_verdict
from app.verify.judges import judge as run_judge

log = get_logger(__name__)

NO_CLAIM_EXPLANATION = (
    "I did not find a factual claim to check in this message. Send me a message, forward, "
    "or rumor that states something as a fact, and I will check it."
)


@dataclass
class PipelineConfig:
    max_claims: int = 3
    research_max_tool_calls: int = 8
    research_time_budget_s: float = 60.0
    match_strong_threshold: float = 0.88
    match_k: int = 3


@dataclass
class Deps:
    llm: LLMClient
    sources: Sequence[Source]
    config: PipelineConfig = field(default_factory=PipelineConfig)
    index: FactCheckIndex | None = None
    media: MediaProviders | None = None
    lid: LanguageDetector | None = None
    panel: Sequence[JudgeSpec] = PANEL  # judges; each may run on its own model


def seen_before_warnings(processed: ProcessedContent) -> list[str]:
    out = []
    for seen in processed.seen_before:
        rated = f" Back then we rated it {seen.last_verdict.value}." if seen.last_verdict else ""
        out.append(f"This picture has been sent to us {seen.times_seen} time(s) before.{rated}")
    return out


async def process_content(
    submission: Submission, deps: Deps, progress: ProgressFn | None
) -> ProcessedContent:
    if deps.media is None:
        return ProcessedContent(text=submission_text(submission))
    with temp_workdir() as workdir:  # raw media is always deleted afterwards
        return await process_submission(submission, deps.media, workdir, progress)


async def match_factchecks(
    index: FactCheckIndex | None, claims: Sequence[Claim], cfg: PipelineConfig
) -> tuple[list[EvidenceDraft], int]:
    """Look claims up in the fact-check index. Returns (evidence, claims with a strong match)."""
    if index is None:
        return [], 0
    drafts: list[EvidenceDraft] = []
    matched = 0
    for claim in claims:
        try:
            hits = await index.search([claim.original, claim.english], k=cfg.match_k)
        except Exception as exc:
            log.warning("match_failed", error=str(exc))
            continue
        strong = [h for h in hits if h.cosine >= cfg.match_strong_threshold]
        matched += bool(strong)
        drafts += [h.to_evidence() for h in strong]
    return drafts, matched


def submission_text(submission: Submission) -> str:
    return "\n".join(p.text for p in submission.parts if p.kind is PartKind.TEXT and p.text)


def _combined_explanation(extraction: Extraction, judgements: Sequence[ClaimJudgement]) -> str:
    if len(judgements) == 1:
        return judgements[0].explanation
    return "\n".join(
        f"- Claim: {extraction.claims[j.claim_index].english} -> {j.verdict.value}: {j.explanation}"
        for j in judgements
    )


def _cited_sources(
    judgements: Sequence[ClaimJudgement], evidence: dict[str, Evidence]
) -> list[Evidence]:
    ids = dict.fromkeys(e for j in judgements for e in j.cited_evidence_ids)
    return sorted((evidence[i] for i in ids if i in evidence), key=lambda e: e.tier)


async def run_check(
    submission: Submission,
    deps: Deps,
    trace: RunTrace | None = None,
    progress: ProgressFn | None = None,
    reply_language: str | None = None,
) -> CheckReport:
    """`reply_language` overrides the detected language (user's language-switch preference)."""
    trace = trace or RunTrace()
    token = current_trace.set(trace)
    try:
        return await _run(submission, deps, trace, progress, reply_language)
    finally:
        current_trace.reset(token)


async def _language_hint(deps: Deps, text: str) -> str | None:
    if deps.lid is None or not text.strip():
        return None
    try:
        guess = await deps.lid.detect(text)
    except Exception as exc:
        log.warning("lid_failed", error=str(exc))
        return None
    return f"{guess.language} (confidence {guess.confidence:.2f})" if guess else None


async def _run(
    submission: Submission,
    deps: Deps,
    trace: RunTrace,
    progress: ProgressFn | None,
    reply_language: str | None,
) -> CheckReport:
    cfg = deps.config
    with trace.timed("modality"):
        processed = await process_content(submission, deps, progress)
    text = processed.text

    with trace.timed("extract"):
        if text.strip():
            extraction = await extract_claims(
                deps.llm,
                text,
                max_claims=cfg.max_claims,
                language_hint=await _language_hint(deps, text),
            )
            own_text = submission_text(submission)  # not our English media labels
            if not extraction.romanized_or_mixed and (
                looks_romanized(extraction.language, own_text) or is_mixed_script(own_text)
            ):
                extraction = extraction.model_copy(update={"romanized_or_mixed": True})
        else:  # e.g. every media part failed to open
            extraction = Extraction(
                language=(submission.language_hint or "en").split("-")[0],
                language_name="the user's language",
                script="",
                romanized_or_mixed=False,
                looks_like_satire_or_opinion=False,
                claims=[],
            )

    evidence: list[Evidence] = []
    judgements: list[ClaimJudgement] = []
    decisions: list[ClaimDecision] = []
    if extraction.claims:
        with trace.timed("match"):
            seed, n_matched = await match_factchecks(deps.index, extraction.claims, cfg)
        # Every claim already has a published fact-check: only a short confirmation search.
        budget = cfg.research_max_tool_calls
        if seed and n_matched == len(extraction.claims):
            budget = min(budget, 2)
        with trace.timed("research"):
            try:
                evidence = await research(
                    deps.llm,
                    deps.sources,
                    extraction.claims,
                    language=extraction.language,
                    max_tool_calls=budget,
                    time_budget_s=cfg.research_time_budget_s,
                    seed=seed,
                )
            except Exception as exc:  # no evidence -> every claim ends Unverified
                log.warning("research_failed", error=str(exc))
        by_id = {e.id: e for e in evidence}
        with trace.timed("verify"):
            decisions = await run_judge(deps.llm, extraction.claims, evidence, panel=deps.panel)
        with trace.timed("ground"):
            decisions = await ground(deps.llm, decisions, by_id)
        judgements = [d.judgement for d in decisions]

    by_id = {e.id: e for e in evidence}
    if judgements:
        overall, confidence = overall_verdict(judgements)
        explanation = _combined_explanation(extraction, judgements)
    else:
        overall, confidence, explanation = Verdict.UNVERIFIED, Confidence.LOW, NO_CLAIM_EXPLANATION

    warnings = topic_rules.warnings(
        extraction, judgements, by_id, sources_disagree=any(d.judges_disagree for d in decisions)
    )
    warnings = processed.warnings + seen_before_warnings(processed) + warnings
    language = reply_language or extraction.language
    if is_lower_accuracy(extraction.language):
        warnings.append(LOWER_ACCURACY_WARNING.format(language=english_name(extraction.language)))
    note = topic_rules.safety_note(extraction.claims, judgements)
    if deps.media and deps.media.hashes:
        for phash in processed.image_hashes:
            try:
                await deps.media.hashes.record(phash, overall)
            except Exception as exc:
                log.warning("hash_record_failed", error=str(exc))
    sources = _cited_sources(judgements, by_id)

    with trace.timed("compose"):
        reply = await compose(
            deps.llm,
            language=language,
            language_name=(english_name(language) if reply_language else extraction.language_name),
            verdict=overall,
            confidence=confidence,
            explanation_en=explanation,
            warnings_en=warnings,
            safety_note_en=note,
        )

    return CheckReport(
        submission_id=submission.id,
        language=extraction.language,
        claims=extraction.claims,
        judgements=judgements,
        decisions=decisions,
        overall=overall,
        overall_confidence=confidence,
        evidence=evidence,
        warnings=warnings,
        reply_text=render_text(reply, overall, sources),
        reply_language=language,
        spoken_text=spoken_text(reply),
        needs_human_review=any(d.needs_human_review for d in decisions),
        review_reasons=list(dict.fromkeys(d.review_reason for d in decisions if d.review_reason)),
    )
