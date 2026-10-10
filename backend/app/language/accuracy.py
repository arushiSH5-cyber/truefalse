"""Per-language accuracy: which languages get a "less tested" warning.

Measured numbers come from the Phase 8 eval harness (`language_accuracy.json`: macro-F1 per
language). Until a language has enough measured examples, a conservative default list of
well-supported languages is used; everything else is flagged as lower-accuracy.
"""

import json
from pathlib import Path

from pydantic import BaseModel

ACCURACY_PATH = Path(__file__).with_name("language_accuracy.json")
MIN_EXAMPLES = 30
MIN_MACRO_F1 = 0.6

# High-resource languages for current LLMs and fact-check coverage (default before measurement).
WELL_SUPPORTED = {
    "en",
    "es",
    "pt",
    "fr",
    "de",
    "it",
    "nl",
    "ru",
    "uk",
    "pl",
    "tr",
    "ar",
    "hi",
    "zh",
    "ja",
    "ko",
    "id",
    "vi",
    "ms",
    "bn",
    "fa",
}


class LanguageStats(BaseModel):
    n: int
    macro_f1: float


def load_stats(path: Path = ACCURACY_PATH) -> dict[str, LanguageStats]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {k: LanguageStats.model_validate(v) for k, v in raw.items()}


def is_lower_accuracy(language: str, stats: dict[str, LanguageStats] | None = None) -> bool:
    code = language.split("-")[0].lower()
    measured = (stats if stats is not None else load_stats()).get(code)
    if measured and measured.n >= MIN_EXAMPLES:
        return measured.macro_f1 < MIN_MACRO_F1
    return code not in WELL_SUPPORTED


LOWER_ACCURACY_WARNING = (
    "TrueFalse is less tested in {language}, so please double-check important information "
    "with a trusted local source."
)
