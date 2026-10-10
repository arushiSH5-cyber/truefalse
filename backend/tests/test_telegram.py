from fastapi.testclient import TestClient

from app.api.telegram import get_messenger, get_prefs, get_queue
from app.core.config import Settings, get_settings
from app.core.models import PartKind
from app.core.prefs import InMemoryPrefsStore
from app.ingestion.common import CHECKING, WELCOME, hash_user
from app.ingestion.handler import CHECK_JOB, handle_inbound
from app.ingestion.telegram import parse_update
from app.main import create_app
from tests.fakes import FakeMessenger, FakeQueue

SALT = "test-salt"


def _update(**message) -> dict:
    base = {
        "message_id": 7,
        "chat": {"id": 42, "type": "private"},
        "from": {"id": 99, "language_code": "es"},
    }
    return {"update_id": 1, "message": base | message}


def test_parse_text() -> None:
    inbound = parse_update(_update(text="  Hola mundo "), SALT)
    assert inbound is not None
    assert inbound.submission.parts[0].text == "Hola mundo"
    assert inbound.submission.language_hint == "es"
    assert inbound.target.chat_id == "42" and inbound.target.reply_to_message_id == "7"
    assert "99" not in inbound.submission.user_hash
    assert inbound.submission.user_hash == hash_user(SALT, "telegram", "99")


def test_parse_command_and_media() -> None:
    assert parse_update(_update(text="/start@TrueFalseBot"), SALT).command == "start"
    photo_only = parse_update(_update(photo=[{"file_id": "s"}, {"file_id": "big"}]), SALT)
    assert not photo_only.has_unsupported_media
    assert photo_only.submission.parts[0].media_ref == "big"
    pdf = parse_update(_update(document={"file_id": "d", "mime_type": "application/pdf"}), SALT)
    assert pdf.has_unsupported_media and pdf.submission.parts == []
    img_doc = parse_update(_update(document={"file_id": "d", "mime_type": "image/png"}), SALT)
    assert img_doc.submission.parts[0].kind is PartKind.IMAGE
    with_caption = parse_update(_update(photo=[{"file_id": "x"}], caption="Is this real?"), SALT)
    assert not with_caption.has_unsupported_media
    assert with_caption.submission.parts[0].kind is PartKind.TEXT
    assert parse_update({"update_id": 2, "edited_message": {}}, SALT) is None


async def test_handler_queues_and_acks_in_user_language() -> None:
    m, q = FakeMessenger(), FakeQueue()
    result = await handle_inbound(parse_update(_update(text="claim"), SALT), m, q)
    assert result == "queued"
    assert m.sent[0][1] == CHECKING["es"]
    assert q.jobs[0][0] == CHECK_JOB
    assert q.jobs[0][1]["target"]["chat_id"] == "42"


async def test_handler_welcome() -> None:
    m, q = FakeMessenger(), FakeQueue()
    assert await handle_inbound(parse_update(_update(text="/start"), SALT), m, q) == "welcome"
    assert m.sent[0][1] == WELCOME and not q.jobs


def _client(messenger: FakeMessenger, queue: FakeQueue) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, telegram_webhook_secret="s3cret", user_hash_salt=SALT
    )  # type: ignore[call-arg]
    app.dependency_overrides[get_messenger] = lambda: messenger
    app.dependency_overrides[get_queue] = lambda: queue
    store = InMemoryPrefsStore()
    app.dependency_overrides[get_prefs] = lambda: store
    return TestClient(app)


def test_webhook_rejects_bad_secret() -> None:
    client = _client(FakeMessenger(), FakeQueue())
    assert client.post("/webhooks/telegram", json=_update(text="x")).status_code == 401
    r = client.post(
        "/webhooks/telegram",
        json=_update(text="x"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert r.status_code == 401


def test_webhook_queues() -> None:
    m, q = FakeMessenger(), FakeQueue()
    r = _client(m, q).post(
        "/webhooks/telegram",
        json=_update(text="Vaccines contain chips"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )
    assert r.status_code == 200 and r.json()["result"] == "queued"
    assert len(q.jobs) == 1


def test_webhook_send_failure_still_200() -> None:
    r = _client(FakeMessenger(fail=True), FakeQueue()).post(
        "/webhooks/telegram",
        json=_update(text="x"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )
    assert r.status_code == 200 and r.json()["result"] == "error"
