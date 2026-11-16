"""Other LLM providers, used as independent judges next to Claude.

Each adapter implements `StructuredLLM` only (one call in, one validated Pydantic object out);
the research tool loop stays on Anthropic. SDKs are optional extras and imported lazily:
`uv sync --extra openai` / `uv sync --extra gemini`. Tests inject fake clients.
"""

import base64
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.llm import Effort, ImageInput, LLMError, LLMRefusal
from app.core.logging import get_logger
from app.core.trace import record_tokens

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

PROVIDERS = ("anthropic", "openai", "google")
_ALIASES = {"claude": "anthropic", "gemini": "google"}

# OpenAI's reasoning_effort has no xhigh/max.
OPENAI_EFFORT: dict[str, str] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
    "max": "high",
}


def parse_model_specs(value: str) -> list[tuple[str, str]]:
    """ "anthropic:claude-opus-5-5, openai:<model>" -> [(provider, model), ...]."""
    out: list[tuple[str, str]] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        provider, sep, model = item.partition(":")
        provider = _ALIASES.get(provider.strip().lower(), provider.strip().lower())
        if not sep or not model.strip() or provider not in PROVIDERS:
            raise ValueError(
                f"bad model spec {item!r}; use provider:model, provider in {PROVIDERS}"
            )
        out.append((provider, model.strip()))
    return out


async def validated[R: BaseModel](stage: str, call: Callable[[], Awaitable[R]]) -> R:
    """Run a structured call; retry once if the model's JSON does not match the schema."""
    for attempt in (1, 2):
        try:
            return await call()
        except ValidationError as exc:
            if attempt == 2:
                raise LLMError(f"{stage}: invalid structured output: {exc}") from exc
            log.warning("llm_invalid_output_retry", stage=stage)
    raise AssertionError("unreachable")


class OpenAILLM:
    """OpenAI chat completions with a Pydantic `response_format` (strict JSON schema)."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout_s: float = 120.0,
        max_retries: int = 2,
        send_reasoning_effort: bool = True,
        price_per_mtok: tuple[float, float] | None = None,
        client: Any = None,
    ) -> None:
        if client is None:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=api_key, timeout=timeout_s, max_retries=max_retries)
        self._client = client
        self.model = model
        self._send_effort = send_reasoning_effort
        self._price = price_per_mtok

    async def structured(
        self,
        *,
        stage: str,
        system: str,
        user: str,
        output: type[T],
        effort: Effort = "medium",
        max_tokens: int = 8000,
        images: Sequence[ImageInput] = (),
    ) -> T:
        content: Any = user
        if images:
            content = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{i.media_type};base64,{i.data_b64}"},
                }
                for i in images
            ] + [{"type": "text", "text": user}]
        extra: dict[str, Any] = {}
        if self._send_effort:
            extra["reasoning_effort"] = OPENAI_EFFORT[effort]

        async def call() -> T:
            try:
                response = await self._client.chat.completions.parse(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": content},
                    ],
                    response_format=output,
                    max_completion_tokens=max_tokens,
                    **extra,
                )
            except ValidationError:
                raise
            except Exception as exc:  # SDK errors (API, length limit) -> our error type
                raise LLMError(f"{stage}: {exc}") from exc
            usage = getattr(response, "usage", None)
            if usage is not None:
                record_tokens(
                    stage, usage.prompt_tokens or 0, usage.completion_tokens or 0, self._price
                )
            message = response.choices[0].message
            if getattr(message, "refusal", None):
                log.warning("llm_refusal", stage=stage, provider="openai")
                raise LLMRefusal(stage)
            parsed = message.parsed
            if parsed is None:
                raise LLMError(f"{stage}: no parsed output")
            # Re-validate: never trust a provider's parse step alone.
            return output.model_validate(
                parsed.model_dump() if isinstance(parsed, BaseModel) else parsed
            )

        return await validated(stage, call)


class GeminiLLM:
    """Google Gemini (google-genai SDK) with `response_schema` JSON output."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        price_per_mtok: tuple[float, float] | None = None,
        client: Any = None,
    ) -> None:
        if client is None:
            from google import genai

            client = genai.Client(api_key=api_key)
        self._client = client
        self.model = model
        self._price = price_per_mtok

    async def structured(
        self,
        *,
        stage: str,
        system: str,
        user: str,
        output: type[T],
        effort: Effort = "medium",  # Gemini thinking is left at the model default
        max_tokens: int = 8000,
        images: Sequence[ImageInput] = (),
    ) -> T:
        contents: list[Any] = [
            {"inline_data": {"mime_type": i.media_type, "data": base64.b64decode(i.data_b64)}}
            for i in images
        ] + [user]
        config = {
            "system_instruction": system,
            "response_mime_type": "application/json",
            "response_schema": output,
            "max_output_tokens": max_tokens,
        }

        async def call() -> T:
            try:
                response = await self._client.aio.models.generate_content(
                    model=self.model, contents=contents, config=config
                )
            except Exception as exc:
                raise LLMError(f"{stage}: {exc}") from exc
            usage = getattr(response, "usage_metadata", None)
            if usage is not None:
                record_tokens(
                    stage,
                    usage.prompt_token_count or 0,
                    usage.candidates_token_count or 0,
                    self._price,
                )
            text = getattr(response, "text", None)
            if not text:
                # Empty text usually means a safety block or no candidates.
                log.warning("llm_refusal", stage=stage, provider="google")
                raise LLMRefusal(stage)
            return output.model_validate_json(text)

        return await validated(stage, call)
