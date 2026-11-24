"""Instagram DM and Facebook Messenger webhooks: GET verification handshake + signed POST events."""

import hmac
from functools import lru_cache
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import SecretStr

from app.api.telegram import get_prefs, get_queue, get_reviews, get_translator
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.models import Platform
from app.core.prefs import PrefsStore
from app.core.review import ReviewStore
from app.ingestion.base import JobQueue
from app.ingestion.handler import handle_inbound
from app.ingestion.meta_dm import MetaDMClient, parse_dm_webhook
from app.ingestion.whatsapp import verify_signature
from app.respond.translate import Translator

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
log = get_logger(__name__)


def dm_config(
    settings: Settings, platform: Platform
) -> tuple[SecretStr | None, SecretStr | None, SecretStr | None]:
    """(access token, app secret, verify token) for Instagram or Messenger."""
    if platform is Platform.INSTAGRAM:
        return (
            settings.instagram_token,
            settings.instagram_app_secret,
            settings.instagram_verify_token,
        )
    return (
        settings.messenger_page_token,
        settings.messenger_app_secret,
        settings.messenger_verify_token,
    )


@lru_cache
def _client(token: str, platform: Platform, version: str, timeout_s: float) -> MetaDMClient:
    return MetaDMClient(httpx.AsyncClient(timeout=timeout_s), token, platform, version)


def _verify(
    settings: Settings,
    platform: Platform,
    mode: str | None,
    token: str | None,
    challenge: str | None,
) -> Response:
    expected = dm_config(settings, platform)[2]
    if (
        mode == "subscribe"
        and expected
        and token
        and challenge
        and hmac.compare_digest(token, expected.get_secret_value())
    ):
        return Response(challenge, media_type="text/plain")
    raise HTTPException(403, "verification failed")


async def _events(
    platform: Platform,
    request: Request,
    settings: Settings,
    queue: JobQueue,
    prefs: PrefsStore,
    translator: Translator | None,
    reviews: ReviewStore,
    signature: str | None,
) -> dict[str, Any]:
    token, secret, _ = dm_config(settings, platform)
    if token is None or not token.get_secret_value().strip():
        raise HTTPException(503, f"{platform.value} is not configured")
    body = await request.body()  # raw bytes: the signature is over the exact payload
    if secret is None or not verify_signature(secret.get_secret_value(), body, signature):
        raise HTTPException(401, "bad signature")
    messenger = _client(
        token.get_secret_value(), platform, settings.meta_api_version, settings.http_timeout_seconds
    )
    salt = settings.user_hash_salt.get_secret_value()
    results = []
    for inbound in parse_dm_webhook(await request.json(), salt, platform):
        try:
            results.append(
                await handle_inbound(inbound, messenger, queue, prefs, translator, reviews)
            )
        except Exception as exc:  # always 200 so Meta doesn't retry-storm
            log.exception("dm_handle_failed", platform=platform.value, error=str(exc))
            results.append("error")
    return {"ok": True, "results": results}


Mode = Annotated[str | None, Query(alias="hub.mode")]
VerifyToken = Annotated[str | None, Query(alias="hub.verify_token")]
Challenge = Annotated[str | None, Query(alias="hub.challenge")]
Signature = Annotated[str | None, Header(alias="X-Hub-Signature-256")]


@router.get("/instagram")
async def instagram_verify(
    settings: Annotated[Settings, Depends(get_settings)],
    mode: Mode = None,
    token: VerifyToken = None,
    challenge: Challenge = None,
) -> Response:
    return _verify(settings, Platform.INSTAGRAM, mode, token, challenge)


@router.post("/instagram")
async def instagram_events(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    queue: Annotated[JobQueue, Depends(get_queue)],
    prefs: Annotated[PrefsStore, Depends(get_prefs)],
    translator: Annotated[Translator | None, Depends(get_translator)],
    reviews: Annotated[ReviewStore, Depends(get_reviews)],
    signature: Signature = None,
) -> dict[str, Any]:
    return await _events(
        Platform.INSTAGRAM, request, settings, queue, prefs, translator, reviews, signature
    )


@router.get("/messenger")
async def messenger_verify(
    settings: Annotated[Settings, Depends(get_settings)],
    mode: Mode = None,
    token: VerifyToken = None,
    challenge: Challenge = None,
) -> Response:
    return _verify(settings, Platform.MESSENGER, mode, token, challenge)


@router.post("/messenger")
async def messenger_events(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    queue: Annotated[JobQueue, Depends(get_queue)],
    prefs: Annotated[PrefsStore, Depends(get_prefs)],
    translator: Annotated[Translator | None, Depends(get_translator)],
    reviews: Annotated[ReviewStore, Depends(get_reviews)],
    signature: Signature = None,
) -> dict[str, Any]:
    return await _events(
        Platform.MESSENGER, request, settings, queue, prefs, translator, reviews, signature
    )
