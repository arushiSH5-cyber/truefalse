"""Phase 7: WhatsApp webhook/client, web API, job store, share card, web worker flow."""

import hashlib
import hmac
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api.telegram import get_prefs, get_queue
from app.api.web import get_jobs, get_limiter
from app.api.whatsapp import get_whatsapp
from app.core.config import Settings, get_settings
from app.core.jobs import InMemoryJobStore, WebMessenger, WebUploadFetcher
from app.core.llm import LLMError
from app.core.models import (
    Extraction,
    Part,
    PartKind,
    Platform,
    ReplyTarget,
    Submission,
)
from app.core.prefs import InMemoryPrefsStore
from app.core.ratelimit import InMemoryRateLimiter
from app.ingestion.router import RoutingMessenger
from app.ingestion.whatsapp import WhatsAppClient, parse_webhook, split_text, verify_signature
from app.main import create_app
from app.pipeline import Deps
from app.respond.share_card import render_card
from app.worker import process
from tests.fakes import FakeLLM, FakeMessenger, FakeQueue

SECRET, VERIFY = "app-secret", "verify-me"


def _wa_payload(*messages: dict) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "contacts": [{"wa_id": "15550001111"}],
                            "messages": list(messages),
                        },
                    }
                ],
            }
        ],
    }


TEXT_MSG = {
    "from": "15550001111",
    "id": "wamid.1",
    "type": "text",
    "text": {"body": "Garlic cures covid?"},
}


# ---- WhatsApp parsing & signature ----------------------------------------------------------


def test_signature() -> None:
    body = b'{"a":1}'
    good = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert verify_signature(SECRET, body, good)
    assert not verify_signature(SECRET, body + b" ", good)
    assert not verify_signature(SECRET, body, None) and not verify_signature(SECRET, body, "md5=x")


def test_parse_webhook_kinds() -> None:
    msgs = [
        TEXT_MSG,
        {"from": "2", "id": "w2", "type": "image", "image": {"id": "m1", "caption": "real?"}},
        {"from": "3", "id": "w3", "type": "audio", "audio": {"id": "m2", "voice": True}},
        {
            "from": "4",
            "id": "w4",
            "type": "document",
            "document": {"id": "m3", "mime_type": "application/pdf"},
        },
        {"from": "5", "id": "w5", "type": "text", "text": {"body": "/start"}},
    ]
    t, img, voice, pdf, start = parse_webhook(_wa_payload(*msgs), "salt")
    assert t.submission.parts[0].text == "Garlic cures covid?"
    assert t.target.chat_id == "15550001111" and "15550001111" not in t.submission.user_hash
    assert [p.kind for p in img.submission.parts] == [PartKind.TEXT, PartKind.IMAGE]
    assert voice.submission.parts[0].media_ref == "m2"
    assert pdf.has_unsupported_media and start.command == "start"
    statuses = {"entry": [{"changes": [{"value": {"statuses": [{"id": "x"}]}}]}]}
    assert parse_webhook(statuses, "salt") == []


def test_split_text() -> None:
    text = ("line\n" * 2000).strip()
    chunks = split_text(text, 4096)
    assert all(len(c) <= 4096 for c in chunks) and "".join(chunks).count("line") == 2000


# ---- WhatsApp client (mocked Graph API) ----------------------------------------------------


async def test_whatsapp_client_send_and_fetch(tmp_path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["authorization"] == "Bearer tok"
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "x"}]})
        if request.url.path.endswith("/media123"):
            return httpx.Response(
                200,
                json={
                    "url": "https://lookaside.fbsbx.com/f",
                    "mime_type": "image/jpeg",
                    "file_size": 4,
                },
            )
        return httpx.Response(200, content=b"\xff\xd8ok")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        wa = WhatsAppClient(http, "tok", "PHONE", "v26.0")
        target = ReplyTarget(platform=Platform.WHATSAPP, chat_id="155", reply_to_message_id="wamid")
        await wa.send_text(target, "hello")
        body = json.loads(seen[0].content)
        assert seen[0].url.path == "/v26.0/PHONE/messages"
        assert body["to"] == "155" and body["context"] == {"message_id": "wamid"}
        assert body["text"]["preview_url"] is False
        path = await wa.fetch(Part(kind=PartKind.IMAGE, media_ref="media123"), tmp_path)
        assert path.read_bytes() == b"\xff\xd8ok" and path.suffix == ".jpg"


# ---- HTTP endpoints ------------------------------------------------------------------------


@pytest.fixture
def env():
    app = create_app()
    m, q, jobs, limiter = FakeMessenger(), FakeQueue(), InMemoryJobStore(), InMemoryRateLimiter()
    settings = Settings(
        _env_file=None,
        whatsapp_app_secret=SECRET,  # type: ignore[call-arg]
        whatsapp_verify_token=VERIFY,
        web_checks_per_hour=3,
    )
    app.dependency_overrides.update(
        {
            get_settings: lambda: settings,
            get_whatsapp: lambda: m,
            get_queue: lambda: q,
            get_prefs: lambda: InMemoryPrefsStore(),
            get_jobs: lambda: jobs,
            get_limiter: lambda: limiter,
        }
    )
    return TestClient(app), m, q, jobs


def test_whatsapp_verify_handshake(env) -> None:
    client = env[0]
    ok = client.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "12345"},
    )
    assert ok.status_code == 200 and ok.text == "12345"
    bad = client.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"},
    )
    assert bad.status_code == 403


def test_whatsapp_events_require_signature(env) -> None:
    client, m, q, _ = env
    body = json.dumps(_wa_payload(TEXT_MSG)).encode()
    assert client.post("/webhooks/whatsapp", content=body).status_code == 401
    sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    r = client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )
    assert r.status_code == 200 and r.json()["results"] == ["queued"]
    assert q.jobs[0][1]["target"]["platform"] == "whatsapp"


HDR = {"X-Client-Id": "client-abc-123", "Accept-Language": "es-MX,es;q=0.9"}


def test_web_check_text_and_poll(env) -> None:
    client, _, q, jobs = env
    r = client.post(
        "/api/check",
        data={"text": "El ajo cura el covid", "url": "https://example.com/a"},
        headers=HDR,
    )
    assert r.status_code == 202
    job_id = r.json()["id"]
    payload = q.jobs[0][1]
    assert payload["target"] == {"platform": "web", "chat_id": job_id, "reply_to_message_id": None}
    sub = payload["submission"]
    assert sub["language_hint"] == "es-MX" and "https://example.com/a" in sub["parts"][0]["text"]
    assert client.get(f"/api/check/{job_id}").json()["status"] == "queued"
    assert client.get("/api/check/doesnotexist").status_code == 404
    assert client.get("/api/check/../../etc").status_code == 404


def test_web_upload_validation_and_rate_limit(env) -> None:
    client, _, q, jobs = env
    png = io.BytesIO()
    Image.new("RGB", (10, 10)).save(png, "PNG")
    r = client.post(
        "/api/check", files={"file": ("meme.png", png.getvalue(), "image/png")}, headers=HDR
    )
    assert r.status_code == 202
    part = q.jobs[-1][1]["submission"]["parts"][0]
    assert part["kind"] == "image" and part["media_ref"] in jobs.uploads
    bad = client.post(
        "/api/check", files={"file": ("a.pdf", b"%PDF", "application/pdf")}, headers=HDR
    )
    assert bad.status_code == 415
    assert client.post("/api/check", data={}, headers=HDR).status_code == 422
    assert client.post("/api/check", data={"text": "x"}).status_code == 422  # no client id
    codes = [
        client.post("/api/check", data={"text": "x"}, headers=HDR).status_code for _ in range(3)
    ]
    assert codes[-1] == 429  # limit is 3/hour; 2 already used above


# ---- web worker flow, uploads, share card --------------------------------------------------


async def test_web_job_end_to_end_and_card(env) -> None:
    client, _, _, jobs = env
    job = await jobs.create()
    llm = (
        FakeLLM()
        .queue(
            "extract",
            Extraction(
                language="es",
                language_name="Spanish",
                script="Latin",
                romanized_or_mixed=False,
                looks_like_satire_or_opinion=False,
                claims=[],
            ),
        )
        .queue("compose", LLMError("x"))
    )
    messenger = RoutingMessenger({Platform.WEB: WebMessenger(jobs)})
    sub = Submission(
        platform=Platform.WEB, user_hash="u", parts=[Part(kind=PartKind.TEXT, text="hola")]
    )
    payload = {
        "submission": sub.model_dump(mode="json"),
        "target": {"platform": "web", "chat_id": job.id, "reply_to_message_id": None},
    }
    assert await process(payload, Deps(llm, []), messenger, (4, 20)) == "ok"
    rec = await jobs.get(job.id)
    assert rec.status == "done" and rec.result.verdict == "unverified"
    assert rec.result.verdict_label == "UNVERIFIED"
    card = client.get(f"/api/check/{job.id}/card.png")
    assert card.status_code == 200 and card.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(card.content)).size == (1080, 1080)


async def test_upload_fetch_deletes(tmp_path) -> None:
    jobs = InMemoryJobStore()
    key = await jobs.put_upload(b"abc", "../../evil.png")
    path = await WebUploadFetcher(jobs).fetch(Part(kind=PartKind.IMAGE, media_ref=key), tmp_path)
    assert path.parent == tmp_path and path.suffix == ".png" and path.read_bytes() == b"abc"
    assert key not in jobs.uploads
    with pytest.raises(ValueError):
        await WebUploadFetcher(jobs).fetch(Part(kind=PartKind.IMAGE, media_ref=key), tmp_path)


@pytest.mark.parametrize(
    ("claim", "label"),
    [
        ("Garlic cures COVID-19", "False"),
        ("गर्म नींबू पानी कैंसर ठीक करता है", "गलत"),
        ("الماء الساخن مع الليمون يعالج السرطان", "خطأ"),
        ("热柠檬水可以治愈癌症", "错误"),
    ],
)
def test_render_card_any_script(tmp_path, claim, label) -> None:
    png = render_card(
        verdict="false",
        verdict_label=label,
        claim=claim,
        explanation="Health agencies say there is no evidence for this. " * 3,
        source="who.int",
        font_dir=tmp_path,
    )
    assert Image.open(io.BytesIO(png)).size == (1080, 1080)
