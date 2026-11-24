"""POST /api/translate: same count and order back, rate limited, needs a client id."""

from fastapi.testclient import TestClient

from app.api import web
from app.core.config import Settings, get_settings
from app.core.ratelimit import InMemoryRateLimiter
from app.main import create_app


class EchoLLM:
    def __init__(self, drop: bool = False) -> None:
        self.drop = drop

    async def structured(self, *, user: str, output, **_: object):
        texts = [line.split(">", 1)[1].rsplit("<", 1)[0] for line in user.splitlines()[2:]]
        texts = [f"ES:{t}" for t in texts]
        return output(texts=texts[:-1] if self.drop else texts)


def _client(monkeypatch, llm) -> TestClient:
    monkeypatch.setattr(web, "_engine", lambda settings, http: llm)
    app = create_app()
    limiter = InMemoryRateLimiter()
    app.dependency_overrides.update(
        {get_settings: lambda: Settings(_env_file=None), web.get_limiter: lambda: limiter}
    )
    return TestClient(app)


HDR = {"X-Client-Id": "client12345"}


def test_translate_keeps_order(monkeypatch) -> None:
    client = _client(monkeypatch, EchoLLM())
    r = client.post(
        "/api/translate", json={"language": "es", "texts": ["False", "Sources"]}, headers=HDR
    )
    assert r.status_code == 200 and r.json()["texts"] == ["ES:False", "ES:Sources"]


def test_translate_rejects_bad_input_and_short_answers(monkeypatch) -> None:
    client = _client(monkeypatch, EchoLLM(drop=True))
    assert (
        client.post(
            "/api/translate", json={"language": "es", "texts": ["a", "b"]}, headers=HDR
        ).status_code
        == 502
    )
    assert (
        client.post(
            "/api/translate", json={"language": "x y", "texts": ["a"]}, headers=HDR
        ).status_code
        == 422
    )
    assert client.post("/api/translate", json={"language": "es", "texts": ["a"]}).status_code == 422
