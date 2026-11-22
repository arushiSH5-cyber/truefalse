"""Gemini as the main engine, and local mode. Offline: HTTP is mocked, keys are made up."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app import local
from app.core import gemini
from app.core.config import Settings
from app.core.container import build_deps
from app.core.llm import ImageInput, LLMError, LLMRefusal, Tool
from app.research.sources.wikipedia import WikipediaSource


class Answer(BaseModel):
    word: str


def _ok(*parts: dict, finish: str = "STOP") -> httpx.Response:
    body = {
        "candidates": [{"content": {"role": "model", "parts": list(parts)}, "finishReason": finish}]
    }
    return httpx.Response(200, json=body)


def _llm(handler, models=("m-one", "m-two")) -> gemini.GeminiMainLLM:  # type: ignore[no-untyped-def]
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return gemini.GeminiMainLLM(http, "fake-key", models)


async def test_structured_sends_schema_image_and_key_in_header() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok({"text": "hmm", "thought": True}, {"text": '{"word":"hi"}'})

    out = await _llm(handler).structured(
        stage="t", system="s", user="u", output=Answer, images=[ImageInput("image/png", "AAAA")]
    )
    assert out.word == "hi"
    assert "fake-key" not in str(seen[0].url) and seen[0].headers["x-goog-api-key"] == "fake-key"
    body = json.loads(seen[0].content)
    assert body["contents"][0]["parts"][0]["inlineData"]["mimeType"] == "image/png"
    assert body["generationConfig"]["responseJsonSchema"]["required"] == ["word"]


async def test_used_up_model_falls_through_to_the_next() -> None:
    urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return (
            httpx.Response(429, json={}) if "m-one" in urls[-1] else _ok({"text": '{"word":"x"}'})
        )

    assert (
        await _llm(handler).structured(stage="t", system="", user="", output=Answer)
    ).word == "x"
    assert len(urls) == 2


async def test_bad_key_fails_at_once_without_leaking_it() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(403, json={"error": {"message": "API key not valid"}})

    with pytest.raises(LLMError) as err:
        await _llm(handler).structured(stage="t", system="", user="", output=Answer)
    assert len(calls) == 1 and "fake-key" not in str(err.value)


async def test_safety_block_is_a_refusal() -> None:
    with pytest.raises(LLMRefusal):
        await _llm(lambda r: _ok(finish="SAFETY")).structured(
            stage="t", system="", user="", output=Answer
        )


async def test_tool_loop_echoes_signatures_and_respects_budget() -> None:
    bodies, ran = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        call = {
            "functionCall": {"name": "look", "args": {"q": "x"}, "id": "c1"},
            "thoughtSignature": "sig",
        }
        return _ok(call)

    async def look(args: dict) -> str:
        ran.append(args)
        return "found"

    tool = Tool("look", "Look", {"type": "object"}, look)
    n = await _llm(handler).run_tools(
        stage="r", system="s", user="u", tools=[tool], max_tool_calls=2, time_budget_s=5
    )
    assert n == len(ran) == 2 and len(bodies) == 2
    model_turn, reply = bodies[1]["contents"][1:3]
    assert model_turn["parts"][0]["thoughtSignature"] == "sig"
    assert reply["parts"][0]["functionResponse"]["id"] == "c1"


async def test_gemini_key_alone_builds_the_whole_pipeline() -> None:
    settings = Settings(_env_file=None, gemini_api_key="fake", verdict_cache_enabled=False)  # type: ignore[call-arg]
    async with httpx.AsyncClient() as http:
        deps = build_deps(settings, http)
    assert isinstance(deps.llm, gemini.GeminiMainLLM) and len(deps.panel) == 3
    assert isinstance(deps.media.stt, gemini.GeminiSTT)
    assert any(isinstance(s, WikipediaSource) for s in deps.sources)


async def test_wikipedia_edition_cannot_change_the_host() -> None:
    hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.params.get("list") == "search":
            return httpx.Response(200, json={"query": {"search": [{"pageid": 1}]}})
        page = {
            "title": "Garlic",
            "fullurl": "https://en.wikipedia.org/wiki/Garlic",
            "extract": "A plant.",
        }
        return httpx.Response(200, json={"query": {"pages": [page]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        found = await WikipediaSource(http).search("garlic", "evil.example/#")
    assert set(hosts) == {"en.wikipedia.org"} and found[0].tier == 3


def test_local_mode_without_a_key_says_so(monkeypatch, tmp_path) -> None:
    for name in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=\n")
    monkeypatch.setattr(local, "ENV_FILE", env)
    with TestClient(local.create_local_app()) as client:
        assert client.get("/api/status").json()["ready"] is False
        res = client.post("/api/check", data={"text": "x"}, headers={"X-Client-Id": "client-abc-1"})
        assert res.status_code == 503 and "set_key.py" in res.json()["detail"]
        env.write_text("GEMINI_API_KEY=made-up\n")  # picked up without a restart
        assert client.get("/api/status").json()["engine"] == "gemini"


def test_daily_limit_protects_the_key_on_a_public_link(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("DAILY_CHECK_LIMIT", raising=False)
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=made-up\nDAILY_CHECK_LIMIT=2\nWEB_CHECKS_PER_HOUR=99\n")
    monkeypatch.setattr(local, "ENV_FILE", env)

    class Queue:
        full = False

        async def enqueue(self, function: str, payload: dict) -> None:
            pass

    with TestClient(local.create_local_app(queue=Queue())) as client:
        codes = [
            client.post(
                "/api/check", data={"text": "x"}, headers={"X-Client-Id": f"someone-{i:04d}"}
            ).status_code
            for i in range(3)
        ]
    assert codes == [202, 202, 503]  # a new client id per request doesn't get around it
