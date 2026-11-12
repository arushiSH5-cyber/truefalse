"""Normalize fact-checkers' free-text ratings ("Falso", "Pants on Fire", "Trompeur", ...).

A fast keyword table covers common ratings. Anything it cannot read (another language, a
house style like "Four Pinocchios", or a negated "true") goes to an LLM fallback, so ratings
in any language count.
"""

import json
import re
import unicodedata
from collections.abc import Sequence

from pydantic import BaseModel, Field

from app.core import prompts
from app.core.llm import StructuredLLM
from app.core.logging import get_logger
from app.core.models import Evidence, Verdict

log = get_logger(__name__)

# Checked in order: "mostly false" must hit MISLEADING before the plain "false" rule.
_RULES: list[tuple[Verdict, tuple[str, ...]]] = [
    (
        Verdict.UNVERIFIED,
        (
            "unverified",
            "unverifiable",
            "unproven",
            "undetermined",
            "inconclusive",
            "unclear",
            "no verificado",
            "não verificado",
            "non vérifié",
            "nicht überprüfbar",
            "अपुष्ट",
        ),
    ),
    (
        Verdict.MISLEADING,
        (
            "mostly false",
            "half true",
            "half-true",
            "mixture",
            "mixed",
            "misleading",
            "missing context",
            "out of context",
            "lacks context",
            "partly false",
            "partially false",
            "partly true",
            "partially true",
            "exaggerat",
            "inexact",
            "imprecise",
            "distort",
            "cherry",
            "needs context",
            "inexacto",
            "engañoso",
            "enganoso",
            "enganosa",
            "impreciso",
            "verdad a medias",
            "sin contexto",
            "fuera de contexto",
            "parcialmente",
            "trompeur",
            "partiellement",
            "sans contexte",
            "irreführend",
            "teilweise",
            "fuorviante",
            "parzialmente",
            "distorcido",
            "भ्रामक",
            "आंशिक",
            "مضلل",
            "جزئيا",
            "误导",
            "部分",
            "вводит в заблуждение",
            "частично",
            "menyesatkan",
            "sebagian",
            "inapotosha",
            "தவறாக வழிநடத்தும்",
        ),
    ),
    (
        Verdict.FALSE,
        (
            "false",
            "fake",
            "pants on fire",
            "incorrect",
            "wrong",
            "hoax",
            "fabricated",
            "baseless",
            "debunked",
            "not true",
            "not real",
            "untrue",
            "scam",
            "satire",
            "falso",
            "falsa",
            "bulo",
            "faux",
            "fausse",
            "infox",
            "falsch",
            "fälschung",
            "sbagliato",
            "errado",
            "enganação",
            "boato",
            "झूठ",
            "फर्जी",
            "गलत",
            "असत्य",
            "كاذب",
            "زائف",
            "خاطئ",
            "غير صحيح",
            "虚假",
            "错误",
            "谣言",
            "不实",
            "ложь",
            "фейк",
            "неправда",
            "salah",
            "hoaks",
            "palsu",
            "uongo",
            "si kweli",
            "தவறு",
            "பொய்",
            "ভুয়া",
            "মিথ্যা",
            "yanlış",
            "sahte",
        ),
    ),
    (
        Verdict.TRUE,
        (
            "true",
            "correct",
            "accurate",
            "verified",
            "real",
            "verdadero",
            "verdadera",
            "cierto",
            "correcto",
            "verdadeiro",
            "vrai",
            "exact",
            "richtig",
            "wahr",
            "vero",
            "सही",
            "सच",
            "صحيح",
            "真实",
            "正确",
            "правда",
            "верно",
            "benar",
            "kweli",
            "உண்மை",
            "সত্য",
            "doğru",
        ),
    ),
]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


# Words that turn a "true" rating around ("Non vero", "No es cierto", "सही नहीं", "doğru değil").
NEGATIONS = frozenset(
    {
        "not",
        "no",
        "non",
        "não",
        "nao",
        "pas",
        "nicht",
        "kein",
        "keine",
        "niet",
        "geen",
        "nie",
        "ei",
        "değil",
        "tidak",
        "bukan",
        "si",
        "не",
        "нет",
        "ні",
        "नहीं",
        "ना",
        "नाही",
        "ليس",
        "ليست",
        "لا",
        "نہیں",
        "இல்லை",
        "அல்ல",
        "নয়",
        "নেই",
        "না",
    }
)
# Negations written inside the word: "unwahr", "неверно", "不正确", "உண்மையல்ல".
NEGATED_PREFIXES = ("un", "не")
NEGATION_CHARS = ("不", "非", "没", "沒", "ไม่", "ல்ல")
_SPLIT = re.compile(r"[\s,.;:!?¡¿'\"()\[\]/«»“”‘’-]+")


def _negated(text: str, needle: str) -> bool:
    if set(_SPLIT.split(text)) & NEGATIONS or any(c in text for c in NEGATION_CHARS):
        return True
    i = text.find(needle)
    return any(text[max(0, i - len(p)) : i] == p for p in NEGATED_PREFIXES)


def normalize_rating(rating: str | None) -> Verdict | None:
    """Map a textual rating to a verdict, or None if it is unknown/unclassifiable."""
    if not rating:
        return None
    text = _norm(rating)
    for verdict, needles in _RULES:
        hit = next((n for n in needles if n in text), None)
        if hit is None:
            continue
        # A negated "true" is not a "true". Leave it to the LLM fallback rather than guess.
        if verdict is Verdict.TRUE and _negated(text, hit):
            return None
        return verdict
    return None


def rating_verdict(evidence: Evidence) -> Verdict | None:
    """Best known mapping for one piece of evidence: LLM fallback first, then the table."""
    return evidence.rating_verdict or normalize_rating(evidence.rating)


class RatingLabel(BaseModel):
    index: int = Field(description="Index of the rating in the input list")
    verdict: Verdict | None = Field(
        description="The rating on our scale, or null if it does not say whether the claim "
        "is true or false"
    )


class RatingLabels(BaseModel):
    labels: list[RatingLabel]


_CACHE: dict[str, Verdict | None] = {}
_CACHE_MAX = 5000


async def resolve_ratings(llm: StructuredLLM, evidence: Sequence[Evidence]) -> list[Evidence]:
    """Fill `rating_verdict` for ratings the keyword table cannot read. Never raises."""
    unknown = sorted(
        {e.rating for e in evidence if e.rating and normalize_rating(e.rating) is None}
        - _CACHE.keys()
    )
    if unknown:
        try:
            out = await llm.structured(
                stage="ratings",
                system=prompts.render("rating_normalize.v1"),
                user=json.dumps(list(enumerate(unknown)), ensure_ascii=False),
                output=RatingLabels,
                effort="low",
                max_tokens=2000,
            )
            if len(_CACHE) > _CACHE_MAX:
                _CACHE.clear()
            for label in out.labels:
                if 0 <= label.index < len(unknown):
                    _CACHE[unknown[label.index]] = label.verdict
        except Exception as exc:  # unknown ratings are then simply ignored, as before
            log.warning("rating_fallback_failed", error=str(exc))
    return [
        e.model_copy(update={"rating_verdict": _CACHE[e.rating]})
        if e.rating in _CACHE and e.rating_verdict is None
        else e
        for e in evidence
    ]


def polarity(verdict: Verdict | None) -> int:
    """+1 supports the claim, -1 contradicts it, 0 neutral/unknown."""
    if verdict is Verdict.TRUE:
        return 1
    if verdict in (Verdict.FALSE, Verdict.MISLEADING):
        return -1
    return 0
