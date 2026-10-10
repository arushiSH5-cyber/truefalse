"""Translate fixed UI text (welcome, confirmations, digests) into any language, with caching."""

from pydantic import BaseModel

from app.core import prompts
from app.core.llm import LLMClient
from app.core.logging import get_logger

log = get_logger(__name__)


class Translation(BaseModel):
    text: str


class Translator:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self._cache: dict[tuple[str, str], str] = {}

    async def translate(self, text: str, language: str) -> str:
        """Best effort: returns the English text if translation fails."""
        base = language.split("-")[0].lower()
        if base == "en" or not text.strip():
            return text
        key = (text, language)
        if key not in self._cache:
            try:
                out = await self._llm.structured(
                    stage="translate",
                    system=prompts.render("translate.v1"),
                    user=f"Target language: {language}\n\n<text>\n{text}\n</text>",
                    output=Translation,
                    effort="low",
                    max_tokens=2000,
                )
                self._cache[key] = out.text
            except Exception as exc:
                log.warning("translate_failed", language=language, error=str(exc))
                return text
        return self._cache[key]
