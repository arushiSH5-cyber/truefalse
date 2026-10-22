"""Multi-model judge panel: provider adapters (fake SDK clients), panel building, voters, cost."""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.container import build_panel
from app.core.llm import FALLBACK_BETA, AnthropicLLM, LLMError, LLMRefusal
from app.core.models import ClaimJudgement, Confidence, JudgePanelOutput, Verdict
from app.core.providers import GeminiLLM, OpenAILLM, parse_model_specs
from app.core.trace import RunTrace, current_trace
from app.verify.judges import PANEL, judge, panel_for
from tests.fakes import FakeLLM

OUT = JudgePanelOutput(
    judgements=[
        ClaimJudgement(
            claim_index=0,
            verdict=Verdict.FALSE,
            confidence=Confidence.HIGH,
            explanation="No.",
            cited_evidence_ids=["E1"],
        )
    ]
)


def _validation_error() -> ValidationError:
    try:
        JudgePanelOutput.model_validate({})
    except ValidationError as exc:
        return exc
    raise AssertionError


# ---- model specs ---------------------------------------------------------------------------


def test_parse_model_specs() -> None:
    assert parse_model_specs(" anthropic:claude-opus-5-5, gemini:g-1 ,openai:o-1,") == [
        ("anthropic", "claude-opus-5-5"),
        ("google", "g-1"),
        ("openai", "o-1"),
    ]
    for bad in ("claude-opus-5-5", "mystery:m", "openai:"):
        with pytest.raises(ValueError):
            parse_model_specs(bad)


# ---- OpenAI adapter ------------------------------------------------------------------------


class FakeOpenAI:
    def __init__(self, *results: Any) -> None:
        self.results = list(results)
        self.kwargs: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self._parse))

    async def _parse(self, **kwargs: Any) -> Any:
        self.kwargs.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        parsed, refusal = result
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=refusal))],
            usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=100),
        )


async def _call(llm: Any) -> JudgePanelOutput:
    out: JudgePanelOutput = await llm.structured(
        stage="verify.x", system="sys", user="u", output=JudgePanelOutput, effort="max"
    )
    return out


async def test_openai_structured_ok_maps_effort_and_prices() -> None:
    client = FakeOpenAI((OUT, None))
    llm = OpenAILLM("k", "o-1", client=client, price_per_mtok=(1.0, 10.0))
    trace = RunTrace()
    token = current_trace.set(trace)
    try:
        assert await _call(llm) == OUT
    finally:
        current_trace.reset(token)
    kw = client.kwargs[0]
    assert kw["response_format"] is JudgePanelOutput and kw["reasoning_effort"] == "high"
    assert kw["messages"][0] == {"role": "system", "content": "sys"}
    # Known price used instead of the default (which would be 4/20 per MTok).
    assert trace.cost_usd(4.0, 20.0) == pytest.approx(0.002)


async def test_openai_no_reasoning_effort_when_disabled() -> None:
    client = FakeOpenAI((OUT, None))
    await _call(OpenAILLM("k", "o-1", client=client, send_reasoning_effort=False))
    assert "reasoning_effort" not in client.kwargs[0]


async def test_openai_refusal_and_errors() -> None:
    with pytest.raises(LLMRefusal):
        await _call(OpenAILLM("k", "m", client=FakeOpenAI((None, "I can't help"))))
    with pytest.raises(LLMError):
        await _call(OpenAILLM("k", "m", client=FakeOpenAI(RuntimeError("500"))))


async def test_openai_retries_once_on_invalid_output() -> None:
    client = FakeOpenAI(_validation_error(), (OUT, None))
    assert await _call(OpenAILLM("k", "m", client=client)) == OUT
    with pytest.raises(LLMError, match="invalid structured output"):
        await _call(
            OpenAILLM("k", "m", client=FakeOpenAI(_validation_error(), _validation_error()))
        )


# ---- Gemini adapter ------------------------------------------------------------------------


class FakeGemini:
    def __init__(self, *texts: str | None) -> None:
        self.texts = list(texts)
        self.kwargs: list[dict[str, Any]] = []
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._gen))

    async def _gen(self, **kwargs: Any) -> Any:
        self.kwargs.append(kwargs)
        return SimpleNamespace(
            text=self.texts.pop(0),
            usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5),
        )


async def test_gemini_structured_ok_and_validates() -> None:
    client = FakeGemini(OUT.model_dump_json())
    assert await _call(GeminiLLM("k", "g-1", client=client)) == OUT
    cfg = client.kwargs[0]["config"]
    assert cfg["response_schema"] is JudgePanelOutput and cfg["system_instruction"] == "sys"


async def test_gemini_invalid_json_retries_then_fails() -> None:
    bad = json.dumps({"judgements": [{"claim_index": 0, "verdict": "maybe"}]})
    assert await _call(GeminiLLM("k", "g", client=FakeGemini(bad, OUT.model_dump_json()))) == OUT
    with pytest.raises(LLMError):
        await _call(GeminiLLM("k", "g", client=FakeGemini(bad, bad)))


async def test_gemini_empty_text_is_refusal() -> None:
    with pytest.raises(LLMRefusal):
        await _call(GeminiLLM("k", "g", client=FakeGemini(None)))


# ---- panel ---------------------------------------------------------------------------------


def test_default_panel_is_three_claude_models_without_haiku_fallbacks() -> None:
    main = AnthropicLLM("k", "claude-opus-5-5")
    panel = build_panel(Settings(anthropic_api_key="k"), main)
    assert [s.model for s in panel] == [
        "anthropic:claude-opus-5-5",
        "anthropic:claude-sonnet-5-5",
        "anthropic:claude-haiku-5-5",
    ]
    assert [s.name for s in panel] == [s.name for s in PANEL]  # one lens each
    opus, sonnet, haiku = (s.llm for s in panel)
    assert opus is main
    assert isinstance(sonnet, AnthropicLLM) and sonnet._betas == [FALLBACK_BETA]
    assert isinstance(haiku, AnthropicLLM) and haiku._fallbacks != "default"
    assert haiku._client is main._client  # one HTTP client shared


def test_panel_skips_missing_keys_and_falls_back_to_single_model() -> None:
    main = AnthropicLLM("k", "claude-opus-5-5")
    settings = Settings(
        anthropic_api_key="k",
        judge_models="anthropic:claude-opus-5-5,openai:o-1,google:g-1",
    )
    panel = build_panel(settings, main)  # no OpenAI/Gemini keys -> only one usable judge
    assert [s.prompt for s in panel] == [s.prompt for s in PANEL]
    assert all(s.llm is main for s in panel)


def test_panel_for_more_judges_than_lenses_has_unique_names() -> None:
    llms = [(f"p:m{i}", FakeLLM()) for i in range(4)]
    names = [s.name for s in panel_for(llms)]
    assert names == ["balanced", "skeptic", "context", "balanced3"]


async def test_judges_run_on_their_own_models_and_votes_record_voters() -> None:
    from tests.test_trust import CLAIM, EVIDENCE

    a, b, c = FakeLLM(), FakeLLM(), FakeLLM()
    panel = panel_for([("anthropic:a", a), ("openai:b", b), ("google:c", c)])
    for spec, llm in zip(panel, (a, b, c), strict=True):
        llm.queue(f"verify.{spec.name}", OUT)
    main = FakeLLM()  # must not be asked to judge
    [decision] = await judge(main, [CLAIM], list(EVIDENCE.values()), panel=panel)
    assert decision.voters == ["anthropic:a", "openai:b", "google:c"]
    assert decision.judgement.verdict is Verdict.FALSE
    assert not [x for x in main.calls if x["stage"].startswith("verify")]
    assert all(len(x.calls) == 1 for x in (a, b, c))
