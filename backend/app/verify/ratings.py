"""Normalize fact-checkers' free-text ratings ("Falso", "Pants on Fire", "Trompeur", ...)."""

import re
import unicodedata

from app.core.models import Verdict

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


def normalize_rating(rating: str | None) -> Verdict | None:
    """Map a textual rating to a verdict, or None if it is unknown/unclassifiable."""
    if not rating:
        return None
    text = _norm(rating)
    for verdict, needles in _RULES:
        if any(n in text for n in needles):
            return verdict
    return None


def polarity(verdict: Verdict | None) -> int:
    """+1 supports the claim, -1 contradicts it, 0 neutral/unknown."""
    if verdict is Verdict.TRUE:
        return 1
    if verdict in (Verdict.FALSE, Verdict.MISLEADING):
        return -1
    return 0
