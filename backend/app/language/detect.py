"""Optional fast language-ID pre-check with fastText (NLLB lid218e or lid.176).

The LLM claim-extraction step is the authority on language (it handles code-mixing and
romanization). fastText gives a cheap, model-independent second opinion that is passed to the
extractor as a hint and logged for per-language accuracy tracking.
Model files: see README (Language ID). Needs the `lid` extra.
"""

import asyncio
from typing import Any, Protocol

from pydantic import BaseModel

from app.language.scripts import dominant_script


class LanguageGuess(BaseModel):
    language: str  # BCP-47 base code, e.g. "hi"
    confidence: float
    script: str | None


class LanguageDetector(Protocol):
    async def detect(self, text: str) -> LanguageGuess | None: ...


# NLLB-style labels ("__label__hin_Deva") -> ISO 639-1 where one exists.
_ISO3_TO_1 = {
    "eng": "en",
    "spa": "es",
    "fra": "fr",
    "deu": "de",
    "por": "pt",
    "ita": "it",
    "nld": "nl",
    "rus": "ru",
    "ukr": "uk",
    "pol": "pl",
    "tur": "tr",
    "arb": "ar",
    "ara": "ar",
    "pes": "fa",
    "urd": "ur",
    "hin": "hi",
    "ben": "bn",
    "pan": "pa",
    "guj": "gu",
    "mar": "mr",
    "tam": "ta",
    "tel": "te",
    "kan": "kn",
    "mal": "ml",
    "npi": "ne",
    "sin": "si",
    "zho": "zh",
    "jpn": "ja",
    "kor": "ko",
    "vie": "vi",
    "tha": "th",
    "ind": "id",
    "zsm": "ms",
    "tgl": "tl",
    "swh": "sw",
    "amh": "am",
    "hau": "ha",
    "yor": "yo",
    "ibo": "ig",
    "zul": "zu",
    "xho": "xh",
    "som": "so",
    "ell": "el",
    "heb": "he",
    "ron": "ro",
    "hun": "hu",
    "ces": "cs",
    "swe": "sv",
    "fin": "fi",
    "dan": "da",
    "nob": "no",
    "khm": "km",
    "mya": "my",
    "kat": "ka",
    "hye": "hy",
    "fil": "tl",
}


def _parse_label(label: str) -> str:
    code = label.removeprefix("__label__").split("_")[0]
    return _ISO3_TO_1.get(code, code)


class FastTextDetector:
    def __init__(self, model_path: str) -> None:
        self._path = model_path
        self._model: Any = None

    def _predict(self, text: str) -> LanguageGuess | None:
        if self._model is None:
            import fasttext

            self._model = fasttext.load_model(self._path)
        clean = " ".join(text.split())[:2000]
        if not clean:
            return None
        labels, probs = self._model.predict(clean, k=1)
        return LanguageGuess(
            language=_parse_label(labels[0]),
            confidence=float(probs[0]),
            script=dominant_script(clean),
        )

    async def detect(self, text: str) -> LanguageGuess | None:
        return await asyncio.to_thread(self._predict, text)
