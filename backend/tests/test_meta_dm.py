"""Instagram DMs and Facebook Messenger: parsing, webhook security, sending, laptop mode."""

import hashlib
import hmac
import json

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.telegram import get_prefs, get_queue, get_reviews, get_translator
from app.core.config import Settings, get_settings
from app.core.models import PartKind, Platform, ReplyTarget
from app.core.prefs import InMemoryPrefsStore
from app.core.review import InMemoryReviewStore
from app.ingestion.common import CHECKING
from app.ingestion.handler import CHECK_JOB
from app.ingestion.meta_dm import MetaDMClient, parse_dm_webhook
from app.main import create_app
from tests.fakes import FakeQueue

SECRET, VERIFY, SALT = "app-secret", "verify-me", "salt"


def _payload(*events: dict, obj: str = "instagram") -> dict:
    return {"object": obj, "entry": [{"id": "1", "time": 1, "messaging": list(events)}]}


def _event(**message) -> dict:
    return {
        "sender": {"id": "IGSID1"},
        "recipient": {"id": "ME"},
        "message": {"mid": "m1"} | message,
    }


def test_parse_text_media_and_shared_post() -> None:
    payload = _payload(
        _event(text="Garlic cures covid"),
        _event(attachments=[{"type": "image", "payload": {"url": "https://cdn.example/a.jpg"}}]),
        _event(
            attachments=[
                {
                    "type": "ig_reel",
                    "payload": {"url": "https://cdn.example/r.mp4", "title": "Miracle cure"},
                }
            ]
        ),
        _event(attachments=[{"type": "share", "payload": {"url": "https://instagram.com/p/x"}}]),
        _event(attachments=[{"type": "audio", "payload": {"url": "https://cdn.example/v.mp4"}}]),
    )
    a, b, c, d, e = parse_dm_webhook(payload, SALT, Platform.INSTAGRAM)
    assert a.submission.parts[0].text == "Garlic cures covid"
    assert a.target == ReplyTarget(
        platform=Platform.INSTAGRAM, chat_id="IGSID1", reply_to_message_id="m1"
    )
    assert b.submission.parts[0].kind is PartKind.IMAGE
    assert b.submission.parts[0].media_ref == "https://cdn.example/a.jpg"
    assert [p.kind for p in c.submission.parts] == [PartKind.TEXT, PartKind.VIDEO]
    assert d.submission.parts[0].text == "https://instagram.com/p/x"
    assert e.submission.parts[0].kind is PartKind.AUDIO


def test_parse_ignores_echoes_reads_and_reactions() -> None:
    payload = _payload(
        _event(text="our own reply", is_echo=True),
        {"sender": {"id": "IGSID1"}, "read": {"mid": "m1"}},
        {"sender": {"id": "IGSID1"}, "reaction": {"mid": "m1", "action": "react"}},
    )
    assert parse_dm_webhook(payload, SALT, Platform.INSTAGRAM) == []


def test_parse_command() -> None:
    (inbound,) = parse_dm_webhook(_payload(_event(text="/start")), SALT, Platform.MESSENGER)
    assert inbound.command == "start" and inbound.submission.parts == []
    assert inbound.submission.platform is Platform.MESSENGER


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        instagram_token=SecretStr("ig-token"),
        instagram_app_secret=SecretStr(SECRET),
        instagram_verify_token=SecretStr(VERIFY),
    )


def _client(queue: FakeQueue) -> TestClient:
    app = create_app()
    app.dependency_overrides.update(
        {
            get_settings: _settings,
            get_queue: lambda: queue,
            get_prefs: InMemoryPrefsStore,
            get_translator: lambda: None,
            get_reviews: InMemoryReviewStore,
        }
    )
    return TestClient(app)


def test_verify_handshake() -> None:
    client = _client(FakeQueue())
    ok = client.get(
        "/webhooks/instagram",
        params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "42"},
    )
    assert ok.status_code == 200 and ok.text == "42"
    bad = client.get(
        "/webhooks/instagram",
        params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"},
    )
    assert bad.status_code == 403
    # Messenger has no verify token configured here, so it refuses.
    assert client.get("/webhooks/messenger", params={"hub.mode": "subscribe"}).status_code == 403


def test_events_need_a_valid_signature(monkeypatch) -> None:
    sent: list[dict] = []

    async def fake_send(self, body):  # no network in tests
        sent.append(body)

    monkeypatch.setattr(MetaDMClient, "_send", fake_send)
    queue = FakeQueue()
    client = _client(queue)
    body = json.dumps(_payload(_event(text="5G spreads covid"))).encode()
    assert client.post("/webhooks/instagram", content=body).status_code == 401
    sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    resp = client.post(
        "/webhooks/instagram",
        content=body,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200 and resp.json()["results"] == ["queued"]
    assert queue.jobs[0][0] == CHECK_JOB
    assert sent[0]["recipient"] == {"id": "IGSID1"} and sent[0]["message"]["text"] == CHECKING["en"]


async def test_client_splits_long_replies_and_downloads_media(tmp_path) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, content=b"jpegbytes")
        return httpx.Response(200, json={"message_id": "x"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MetaDMClient(http, "tok", Platform.INSTAGRAM)
    target = ReplyTarget(platform=Platform.INSTAGRAM, chat_id="IGSID1")
    await client.send_text(target, ("word " * 150 + "\n") * 3)  # > 1000 characters
    posts = [c for c in calls if c.method == "POST"]
    assert len(posts) >= 2
    assert str(posts[0].url) == "https://graph.instagram.com/v26.0/me/messages"
    assert posts[0].headers["Authorization"] == "Bearer tok"
    assert all(len(json.loads(p.content)["message"]["text"]) <= 1000 for p in posts)

    from app.core.models import Part

    path = await client.fetch(
        Part(kind=PartKind.IMAGE, media_ref="https://cdn.example/a.jpg"), tmp_path
    )
    assert path.read_bytes() == b"jpegbytes"
    get = [c for c in calls if c.method == "GET"][0]
    assert "Authorization" not in get.headers  # the token never goes to the CDN
