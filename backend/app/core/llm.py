"""LLM interface + Anthropic implementation.

Pipeline code depends only on `LLMClient`; tests use a fake (tests/fakes.py).
"""

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeVar

import anthropic
from pydantic import BaseModel

from app.core.logging import get_logger
from app.core.trace import record_tokens

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)
Effort = Literal["low", "medium", "high", "xhigh", "max"]

# Server-side refusal fallback: if the primary model declines, the API reroutes in the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(Exception):
    pass


class LLMRefusal(LLMError):
    pass


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[str]]

    def to_api(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "strict": True,
        }


@dataclass(frozen=True)
class ImageInput:
    media_type: str  # image/jpeg, image/png, image/webp, image/gif
    data_b64: str


class LLMClient(Protocol):
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
    ) -> T: ...

    async def run_tools(
        self,
        *,
        stage: str,
        system: str,
        user: str,
        tools: Sequence[Tool],
        max_tool_calls: int,
        time_budget_s: float,
        effort: Effort = "medium",
    ) -> int:
        """Run an agent loop. Returns the number of tool calls executed."""
        ...


class AnthropicLLM:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout_s: float = 120.0,
        max_retries: int = 2,
        use_fallbacks: bool = True,
    ) -> None:
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key, timeout=timeout_s, max_retries=max_retries
        )
        self._model = model
        # Typed as Any: the SDK's betas param is a Literal list and fallbacks accepts Omit.
        self._betas: Any = [FALLBACK_BETA] if use_fallbacks else anthropic.omit
        self._fallbacks: Any = "default" if use_fallbacks else anthropic.omit

    def _usage(self, stage: str, response: Any) -> None:
        usage = getattr(response, "usage", None)
        if usage is not None:
            record_tokens(stage, usage.input_tokens or 0, usage.output_tokens or 0)

    def _check_refusal(self, stage: str, response: Any) -> None:
        if response.stop_reason == "refusal":
            log.warning("llm_refusal", stage=stage)
            raise LLMRefusal(stage)

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
                    "type": "image",
                    "source": {"type": "base64", "media_type": i.media_type, "data": i.data_b64},
                }
                for i in images
            ] + [{"type": "text", "text": user}]
        try:
            response = await self._client.beta.messages.parse(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": content}],
                output_format=output,
                output_config={"effort": effort},
                betas=self._betas,
                fallbacks=self._fallbacks,
            )
        except anthropic.APIError as exc:
            raise LLMError(f"{stage}: {exc}") from exc
        self._usage(stage, response)
        self._check_refusal(stage, response)
        parsed = response.parsed_output
        if parsed is None:
            raise LLMError(f"{stage}: no parsed output (stop_reason={response.stop_reason})")
        return parsed

    async def run_tools(
        self,
        *,
        stage: str,
        system: str,
        user: str,
        tools: Sequence[Tool],
        max_tool_calls: int,
        time_budget_s: float,
        effort: Effort = "medium",
    ) -> int:
        by_name = {t.name: t for t in tools}
        api_tools: list[Any] = [t.to_api() for t in tools]
        messages: list[Any] = [{"role": "user", "content": user}]
        calls = 0
        try:
            async with asyncio.timeout(time_budget_s):
                # Hard stop on iterations even if the model keeps asking for tools.
                for _ in range(max_tool_calls + 2):
                    response = await self._client.beta.messages.create(
                        model=self._model,
                        max_tokens=8000,
                        system=system,
                        messages=messages,
                        tools=api_tools,
                        output_config={"effort": effort},
                        betas=self._betas,
                        fallbacks=self._fallbacks,
                    )
                    self._usage(stage, response)
                    self._check_refusal(stage, response)
                    uses = [b for b in response.content if b.type == "tool_use"]
                    if response.stop_reason != "tool_use" or not uses:
                        break
                    messages.append({"role": "assistant", "content": response.content})
                    results = await asyncio.gather(
                        *(
                            self._run_one(by_name, u, calls + i >= max_tool_calls)
                            for i, u in enumerate(uses)
                        )
                    )
                    calls += sum(1 for u in uses if u.name in by_name)
                    messages.append({"role": "user", "content": list(results)})
        except TimeoutError:
            log.warning("tool_loop_time_budget_hit", stage=stage, calls=calls)
        except anthropic.APIError as exc:
            # Evidence gathered so far is kept by the tool handlers; the judge decides with it.
            log.warning("tool_loop_api_error", stage=stage, error=str(exc), calls=calls)
        return calls

    async def _run_one(
        self, by_name: dict[str, Tool], use: Any, over_budget: bool
    ) -> dict[str, Any]:
        result: dict[str, Any] = {"type": "tool_result", "tool_use_id": use.id}
        tool = by_name.get(use.name)
        if tool is None:
            return result | {"content": f"Unknown tool {use.name}", "is_error": True}
        if over_budget:
            return result | {
                "content": "Tool budget exhausted. Stop searching and finish.",
                "is_error": True,
            }
        try:
            tool_input = use.input if isinstance(use.input, dict) else json.loads(use.input)
            return result | {"content": await tool.handler(tool_input)}
        except Exception as exc:
            log.warning("tool_failed", tool=use.name, error=str(exc))
            return result | {"content": f"Tool error: {exc}", "is_error": True}
