"""Topic-specific safety notes and user-facing warnings (English canonical; translated later)."""

import re
from collections.abc import Sequence
from datetime import UTC, datetime

from app.core.models import Claim, ClaimJudgement, Evidence, Extraction, Verdict

HEALTH_NOTE = (
    "We are not doctors. Please talk to a doctor, nurse, or pharmacist before changing any "
    "treatment."
)
DANGER_NOTE = (
    "Warning: following this advice could harm your health. Do not try it, and do not stop "
    "medicine a doctor gave you."
)
ELECTION_NOTE = "For official voting information, check your national election commission."
EMERGENCY_NOTE = "In an emergency, follow instructions from local officials and emergency services."

OLD_EVIDENCE_YEARS = 3


def safety_note(claims: Sequence[Claim], judgements: Sequence[ClaimJudgement]) -> str | None:
    notes: list[str] = []
    health = [i for i, c in enumerate(claims) if c.topic in ("health", "superstition")]
    if health:
        harmful = any(
            claims[j.claim_index].potentially_harmful
            and j.verdict in (Verdict.FALSE, Verdict.MISLEADING, Verdict.UNVERIFIED)
            for j in judgements
            if j.claim_index in health
        )
        if harmful:
            notes.append(DANGER_NOTE)
        notes.append(HEALTH_NOTE)
    if any(c.topic == "election" for c in claims):
        notes.append(ELECTION_NOTE)
    if any(c.topic == "emergency" for c in claims):
        notes.append(EMERGENCY_NOTE)
    return " ".join(notes) or None


def _year(published: str | None) -> int | None:
    m = re.search(r"(19|20)\d{2}", published or "")
    return int(m.group(0)) if m else None


def warnings(
    extraction: Extraction,
    judgements: Sequence[ClaimJudgement],
    evidence: dict[str, Evidence],
    *,
    sources_disagree: bool = False,
    now: datetime | None = None,
) -> list[str]:
    out: list[str] = []
    if sources_disagree:
        out.append("Trusted sources or our reviewers disagree about this, so treat it with care.")
    if extraction.looks_like_satire_or_opinion:
        out.append("This looks like it may be satire, a joke, or an opinion, not a news report.")
    if extraction.romanized_or_mixed:
        out.append("Your message mixes languages or scripts, so our checking may be less accurate.")
    cited = [evidence[e] for j in judgements for e in j.cited_evidence_ids if e in evidence]
    years = [y for y in (_year(e.published) for e in cited) if y]
    this_year = (now or datetime.now(UTC)).year
    if years and max(years) <= this_year - OLD_EVIDENCE_YEARS:
        out.append(f"The newest source we found is from {max(years)}. Newer information may exist.")
    if any(j.verdict is Verdict.UNVERIFIED for j in judgements) and len(judgements) > 1:
        out.append("We could not check every claim in your message.")
    return out
