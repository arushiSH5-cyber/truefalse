"""Data models shared across pipeline stages."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from app.ai_detect.signals import AIReport


class Platform(StrEnum):
    TELEGRAM = "telegram"
    WHATSAPP = "whatsapp"
    WEB = "web"


class PartKind(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    URL = "url"


class Part(BaseModel):
    kind: PartKind
    text: str | None = None
    media_ref: str | None = None  # platform file id or temp path; raw media is deleted after use
    url: str | None = None


class Submission(BaseModel):
    """Everything a user sent, normalized across platforms."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    platform: Platform
    user_hash: str
    language_hint: str | None = None  # e.g. Telegram's language_code; only a hint
    parts: list[Part]
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ReplyTarget(BaseModel):
    """Where to send the answer. Lives only in the job payload; never persisted."""

    platform: Platform
    chat_id: str
    reply_to_message_id: str | None = None


Topic = Literal["health", "superstition", "election", "emergency", "science", "other"]


class Claim(BaseModel):
    original: str = Field(description="The claim as written by the user, in the original language")
    english: str = Field(description="Faithful English translation used for research")
    topic: Topic
    potentially_harmful: bool = Field(
        description="True if acting on this claim could hurt someone (e.g. unsafe remedy)"
    )
    # Structured frame of the claim {who, did, where, when}; null when the message does not say.
    who: str | None = Field(description="Who or what the claim is about, in English, or null")
    did: str | None = Field(description="What they did, are, or cause, in English, or null")
    where: str | None = Field(description="Place the claim is about, in English, or null")
    when: str | None = Field(description="Date or time period as stated, in English, or null")
    local_languages: list[str] = Field(
        description="BCP-47 codes of languages local news/fact-checkers would use for this claim"
    )

    def frame(self) -> str:
        """Compact "who | did | where | when" line for prompts and search; skips unknown parts."""
        parts = [("who", self.who), ("did", self.did), ("where", self.where), ("when", self.when)]
        return "; ".join(f"{k}: {v}" for k, v in parts if v)


class Extraction(BaseModel):
    language: str = Field(description="BCP-47 code of the main language, e.g. 'es', 'hi', 'sw'")
    language_name: str = Field(description="Language name in English, e.g. 'Spanish'")
    script: str = Field(description="Writing system, e.g. 'Latin', 'Devanagari', 'Arabic'")
    romanized_or_mixed: bool = Field(description="Romanized or code-mixed text")
    looks_like_satire_or_opinion: bool
    claims: list[Claim]


class Verdict(StrEnum):
    TRUE = "true"
    MISLEADING = "misleading"
    FALSE = "false"
    UNVERIFIED = "unverified"


class SourceKind(StrEnum):
    FACTCHECK = "factcheck"
    SEMANTIC_SCHOLAR = "semantic_scholar"
    PUBMED = "pubmed"
    WEB = "web"
    NEWS = "news"


class EvidenceDraft(BaseModel):
    source: SourceKind
    tier: int = Field(ge=1, le=3)
    title: str
    url: str
    quote: str
    published: str | None = None
    publisher: str | None = None
    rating: str | None = None  # a fact-checker's own textual rating, when present


class Evidence(EvidenceDraft):
    id: str
    # The fact-checker's rating mapped to our scale by the LLM fallback when the keyword table
    # cannot read it (any language). None means "use the keyword table" or "unknown".
    rating_verdict: Verdict | None = None


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ClaimJudgement(BaseModel):
    claim_index: int
    verdict: Verdict
    confidence: Confidence
    explanation: str = Field(description="2-3 plain English sentences a grandparent understands")
    cited_evidence_ids: list[str]


class JudgePanelOutput(BaseModel):
    judgements: list[ClaimJudgement]


class ClaimDecision(BaseModel):
    """Final decision for one claim, with the panel's votes kept for transparency/eval."""

    judgement: ClaimJudgement
    votes: list[Verdict]
    voters: list[str] = Field(default_factory=list)  # model label per vote, same order
    agreement: float
    probability: float  # calibrated probability that the verdict is right
    judges_disagree: bool
    # Set when the panel disagrees, a cited fact-check contradicts the verdict, or too few judges
    # could vote. The verdict is then Unverified: we flag for a person, we never guess.
    needs_human_review: bool = False
    review_reason: str | None = None
    grounded_ratio: float = 1.0  # share of explanation sentences kept by the grounding check


class GroundedSentence(BaseModel):
    index: int = Field(description="Index of the sentence in the input list")
    supported: bool = Field(description="True only if the cited evidence states this directly")
    evidence_ids: list[str]


class GroundedExplanation(BaseModel):
    claim_index: int
    sentences: list[GroundedSentence]


class GroundingOutput(BaseModel):
    explanations: list[GroundedExplanation]


class ComposedReply(BaseModel):
    """Translatable text produced by the LLM; links are added by code, never by the model."""

    verdict_label: str
    explanation: str
    confidence_label: str
    warnings: list[str]
    safety_note: str | None
    sources_heading: str
    # Short message to forward to the chat the claim came from; the top source link is added
    # by code. Empty when composing failed.
    correction: str = ""
    forward_heading: str = "Forward this"


class CheckReport(BaseModel):
    submission_id: str
    language: str
    claims: list[Claim]
    judgements: list[ClaimJudgement]
    decisions: list[ClaimDecision] = Field(default_factory=list)
    overall: Verdict
    overall_confidence: Confidence
    evidence: list[Evidence]
    warnings: list[str]
    reply_text: str
    reply_language: str = "en"
    needs_human_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    correction_text: str = ""  # forwardable correction, with the top source link
    reply: ComposedReply | None = None  # the translated pieces of reply_text, for the web app
    cached_claims: int = 0  # claims answered from the verdict cache (repeated forwards)
    # AI-content / recycled-media checks: separate from the claim verdict, never changes it.
    ai_report: AIReport = Field(default_factory=AIReport)
    spoken_text: str = ""  # short version for voice replies (no links)
