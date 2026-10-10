"""Claim extraction + language detection (any language, code-mixed, romanized)."""

from app.core import prompts
from app.core.llm import LLMClient
from app.core.models import Extraction

MAX_INPUT_CHARS = 6000


async def extract_claims(
    llm: LLMClient, text: str, *, max_claims: int, language_hint: str | None = None
) -> Extraction:
    hint = f"<language_id_hint>{language_hint}</language_id_hint>\n" if language_hint else ""
    result = await llm.structured(
        stage="extract",
        system=prompts.render("claim_extract.v1", max_claims=str(max_claims)),
        user=f"{hint}<message>\n{text[:MAX_INPUT_CHARS]}\n</message>",
        output=Extraction,
        effort="low",
    )
    # Keep only non-empty, de-duplicated claims, capped.
    seen: set[str] = set()
    claims = []
    for c in result.claims:
        key = c.english.strip().lower()
        if key and key not in seen:
            seen.add(key)
            claims.append(c)
    return result.model_copy(update={"claims": claims[:max_claims]})
