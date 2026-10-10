"""Tests the AnthropicLLM tool loop against a stubbed SDK client (no network)."""

from types import SimpleNamespace
from typing import Any

import pytest

from app.core.llm import FALLBACK_BETA, AnthropicLLM, LLMRefusal, Tool
from app.core.trace import RunTrace, current_trace


def _resp(stop: str, *blocks: Any) -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=stop,
        content=list(blocks),
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


def _use(i: int, name: str = "search", query: str = "q") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=f"tu{i}", name=name, input={"query": query})


class _StubMessages:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.requests.append(kwargs)
        return self.responses.pop(0)


def _llm(responses: list[SimpleNamespace]) -> tuple[AnthropicLLM, _StubMessages]:
    llm = AnthropicLLM("test-key", "claude-opus-5-5")
    stub = _StubMessages(responses)
    llm._client = SimpleNamespace(beta=SimpleNamespace(messages=stub))  # type: ignore[assignment]
    return llm, stub


def _tool(calls: list[str], fail_on: str | None = None) -> Tool:
    async def handler(args: dict[str, Any]) -> str:
        if args["query"] == fail_on:
            raise RuntimeError("source down")
        calls.append(args["query"])
        return "results"

    return Tool("search", "d", {"type": "object"}, handler)


async def test_loop_runs_tools_and_returns_results() -> None:
    llm, stub = _llm(
        [
            _resp("tool_use", _use(1, query="a"), _use(2, query="bad")),
            _resp("end_turn", SimpleNamespace(type="text", text="done")),
        ]
    )
    calls: list[str] = []
    trace = RunTrace()
    token = current_trace.set(trace)
    try:
        n = await llm.run_tools(
            stage="research",
            system="s",
            user="u",
            tools=[_tool(calls, fail_on="bad")],
            max_tool_calls=5,
            time_budget_s=5,
        )
    finally:
        current_trace.reset(token)
    assert n == 2 and calls == ["a"]
    results = stub.requests[1]["messages"][-1]["content"]
    assert results[0] == {"type": "tool_result", "tool_use_id": "tu1", "content": "results"}
    assert results[1]["is_error"] is True and "source down" in results[1]["content"]
    assert stub.requests[0]["betas"] == [FALLBACK_BETA]
    assert stub.requests[0]["fallbacks"] == "default"
    assert stub.requests[0]["tools"][0]["strict"] is True
    assert trace.stages["research"].input_tokens == 20


async def test_loop_enforces_tool_budget() -> None:
    llm, stub = _llm([_resp("tool_use", _use(i, query=f"q{i}")) for i in range(10)])
    calls: list[str] = []
    await llm.run_tools(
        stage="r", system="s", user="u", tools=[_tool(calls)], max_tool_calls=2, time_budget_s=5
    )
    assert calls == ["q0", "q1"]
    assert len(stub.requests) == 4  # max_tool_calls + 2 iterations, then hard stop
    over = stub.requests[3]["messages"][-1]["content"][0]
    assert over["is_error"] and "budget" in over["content"]


async def test_refusal_raises() -> None:
    llm, _ = _llm([_resp("refusal")])
    with pytest.raises(LLMRefusal):
        await llm.run_tools(
            stage="r", system="s", user="u", tools=[_tool([])], max_tool_calls=2, time_budget_s=5
        )
