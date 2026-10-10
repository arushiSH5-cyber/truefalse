"""Telegram webhook. Register with setWebhook(url=.../webhooks/telegram, secret_token=...)."""

import hmac
from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.prefs import PrefsStore, RedisPrefsStore
from app.core.queue import ArqJobQueue
from app.ingestion.base import JobQueue, Messenger
from app.ingestion.handler import handle_inbound
from app.ingestion.telegram import TelegramMessenger, parse_update

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
log = get_logger(__name__)


@lru_cache
def _messenger(token: str, timeout_s: float) -> TelegramMessenger:
    return TelegramMessenger(token, timeout_s)


@lru_cache
def _queue(redis_url: str) -> ArqJobQueue:
    return ArqJobQueue(redis_url)


def get_messenger(settings: Annotated[Settings, Depends(get_settings)]) -> Messenger:
    if settings.telegram_bot_token is None:
        raise HTTPException(503, "Telegram is not configured")
    return _messenger(settings.telegram_bot_token.get_secret_value(), settings.http_timeout_seconds)


def get_queue(settings: Annotated[Settings, Depends(get_settings)]) -> JobQueue:
    return _queue(settings.redis_url)


@lru_cache
def _prefs(redis_url: str) -> RedisPrefsStore:
    return RedisPrefsStore(redis_url)


def get_prefs(settings: Annotated[Settings, Depends(get_settings)]) -> PrefsStore:
    return _prefs(settings.redis_url)


@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    messenger: Annotated[Messenger, Depends(get_messenger)],
    queue: Annotated[JobQueue, Depends(get_queue)],
    prefs: Annotated[PrefsStore, Depends(get_prefs)],
    secret: Annotated[str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")] = None,
) -> dict[str, Any]:
    expected = settings.telegram_webhook_secret
    if (
        expected is None
        or secret is None
        or not hmac.compare_digest(secret, expected.get_secret_value())
    ):
        raise HTTPException(401, "bad secret")
    data = await request.json()
    inbound = parse_update(data, settings.user_hash_salt.get_secret_value())
    if inbound is None:
        return {"ok": True, "result": "ignored"}
    try:
        result = await handle_inbound(inbound, messenger, queue, prefs)
    except Exception as exc:
        # Always 200 to Telegram so it doesn't retry-storm; the failure is logged.
        log.exception("telegram_handle_failed", error=str(exc))
        result = "error"
    return {"ok": True, "result": result}
