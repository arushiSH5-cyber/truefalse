"""WhatsApp Cloud API webhook: GET verification handshake + signed POST events."""

import hmac
from functools import lru_cache
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response

from app.api.telegram import get_prefs, get_queue, get_translator
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.prefs import PrefsStore
from app.ingestion.base import JobQueue, Messenger
from app.ingestion.handler import handle_inbound
from app.ingestion.whatsapp import WhatsAppClient, parse_webhook, verify_signature
from app.respond.translate import Translator

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
log = get_logger(__name__)


@lru_cache
def _client(token: str, phone_id: str, version: str, timeout_s: float) -> WhatsAppClient:
    return WhatsAppClient(httpx.AsyncClient(timeout=timeout_s), token, phone_id, version)


def get_whatsapp(settings: Annotated[Settings, Depends(get_settings)]) -> Messenger:
    if settings.whatsapp_token is None or not settings.whatsapp_phone_id:
        raise HTTPException(503, "WhatsApp is not configured")
    return _client(
        settings.whatsapp_token.get_secret_value(),
        settings.whatsapp_phone_id,
        settings.whatsapp_api_version,
        settings.http_timeout_seconds,
    )


@router.get("/whatsapp")
async def verify(
    settings: Annotated[Settings, Depends(get_settings)],
    mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> Response:
    expected = settings.whatsapp_verify_token
    if (
        mode == "subscribe"
        and expected
        and token
        and challenge
        and hmac.compare_digest(token, expected.get_secret_value())
    ):
        return Response(challenge, media_type="text/plain")
    raise HTTPException(403, "verification failed")


@router.post("/whatsapp")
async def events(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    messenger: Annotated[Messenger, Depends(get_whatsapp)],
    queue: Annotated[JobQueue, Depends(get_queue)],
    prefs: Annotated[PrefsStore, Depends(get_prefs)],
    translator: Annotated[Translator | None, Depends(get_translator)],
    signature: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
) -> dict[str, Any]:
    body = await request.body()  # raw bytes: the signature is over the exact payload
    secret = settings.whatsapp_app_secret
    if secret is None or not verify_signature(secret.get_secret_value(), body, signature):
        raise HTTPException(401, "bad signature")
    salt = settings.user_hash_salt.get_secret_value()
    results = []
    for inbound in parse_webhook(await request.json(), salt):
        try:
            results.append(await handle_inbound(inbound, messenger, queue, prefs, translator))
        except Exception as exc:  # always 200 so Meta doesn't retry-storm
            log.exception("whatsapp_handle_failed", error=str(exc))
            results.append("error")
    return {"ok": True, "results": results}
