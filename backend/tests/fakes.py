"""Fake implementations of every external dependency, so tests run offline."""

from collections import defaultdict, deque
from collections.abc import Sequence
from typing import Any, TypeVar

from pydantic import BaseModel

from app.core.llm import Effort, LLMError, Tool
from app.core.models import EvidenceDraft, ReplyTarget, SourceKind
from app.core.trace import record_tokens

T = TypeVar("T", bound=BaseModel)


class FakeLLM:
    """Returns scripted outputs per stage and replays scripted tool calls."""

    def __init__(self) -> None:
        self.outputs: dict[str, deque[BaseModel | Exception]] = defaultdict(deque)
        self.tool_script: list[tuple[str, dict[str, Any]]] = []
        self.calls: list[dict[str, Any]] = []

    def queue(self, stage: str, value: BaseModel | Exception) -> "FakeLLM":
        self.outputs[stage].append(value)
        return self

    async def structured(
        self,
        *,
        stage: str,
        system: str,
        user: str,
        output: type[T],
        effort: Effort = "medium",
        max_tokens: int = 8000,
    ) -> T:
        self.calls.append({"stage": stage, "system": system, "user": user})
        record_tokens(stage, 100, 50)
        if not self.outputs[stage]:
            raise LLMError(f"FakeLLM: nothing queued for {stage}")
        value = self.outputs[stage].popleft()
        if isinstance(value, Exception):
            raise value
        assert isinstance(value, output), f"{stage}: queued {type(value)}, wanted {output}"
        return value

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
        self.calls.append({"stage": stage, "system": system, "user": user})
        by_name = {t.name: t for t in tools}
        done = 0
        for name, args in self.tool_script:
            if done >= max_tool_calls:
                break
            if name in by_name:
                await by_name[name].handler(args)
                done += 1
        return done


class FakeSource:
    def __init__(
        self,
        kind: SourceKind,
        results: list[EvidenceDraft] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.kind = kind
        self.results = results or []
        self.error = error
        self.queries: list[tuple[str, str | None]] = []

    async def search(self, query: str, language: str | None = None) -> list[EvidenceDraft]:
        self.queries.append((query, language))
        if self.error:
            raise self.error
        return list(self.results)


class FakeMessenger:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[ReplyTarget, str]] = []
        self.fail = fail

    async def send_text(self, target: ReplyTarget, text: str) -> None:
        if self.fail:
            raise RuntimeError("send failed")
        self.sent.append((target, text))

    async def send_typing(self, target: ReplyTarget) -> None:
        pass


class FakeQueue:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict[str, Any]]] = []

    async def enqueue(self, function: str, payload: dict[str, Any]) -> None:
        self.jobs.append((function, payload))
