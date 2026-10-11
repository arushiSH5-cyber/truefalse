"""WhatsApp Business Cloud API: webhook parsing, signature check, sending, media download.

Docs: https://developers.facebook.com/docs/whatsapp/cloud-api
- Webhook GET handshake: echo hub.challenge when hub.verify_token matches.
- Webhook POST: X-Hub-Signature-256 = "sha256=" + HMAC-SHA256(app_secret, raw body).
"""

import hashlib
import hmac
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from app.core.http import get_json
from app.core.models import Part, PartKind, Platform, ReplyTarget, Submission
from app.ingestion.base import Inbound
from app.ingestion.common import hash_user

GRAPH = "https://graph.facebook.com"
MAX_TEXT = 4096
MAX_MEDIA_BYTES = 25 * 1024 * 1024

_KINDS = {
    "image": PartKind.IMAGE,
    "audio": PartKind.AUDIO,
    "video": PartKind.VIDEO,
    "sticker": PartKind.IMAGE,
}


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def _message_to_inbound(msg: dict[str, Any], salt: str) -> Inbound | None:
    sender = msg.get("from")
    if not sender:
        return None
    mtype = msg.get("type")
    parts: list[Part] = []
    command = None
    unsupported = False
    if mtype == "text":
        body = (msg.get("text") or {}).get("body", "").strip()
        if body.startswith("/"):
            command = body.split()[0][1:].lower()
        elif body:
            parts.append(Part(kind=PartKind.TEXT, text=body))
    elif mtype in _KINDS:
        media = msg.get(mtype) or {}
        caption = (media.get("caption") or "").strip()
        if caption:
            parts.append(Part(kind=PartKind.TEXT, text=caption))
        parts.append(Part(kind=_KINDS[mtype], media_ref=media.get("id")))
    elif mtype == "document":
        doc = msg.get("document") or {}
        kind = _KINDS.get(str(doc.get("mime_type", "")).split("/")[0])
        if kind:
            parts.append(Part(kind=kind, media_ref=doc.get("id")))
        else:
            unsupported = True
    elif mtype == "interactive":  # button replies are handled as plain text
        reply = (msg.get("interactive") or {}).get("button_reply") or {}
        if reply.get("title"):
            parts.append(Part(kind=PartKind.TEXT, text=reply["title"]))
    else:
        unsupported = True

    return Inbound(
        submission=Submission(
            platform=Platform.WHATSAPP,
            user_hash=hash_user(salt, "whatsapp", sender),
            language_hint=None,  # WhatsApp does not share the user's UI language
            parts=parts,
        ),
        target=ReplyTarget(
            platform=Platform.WHATSAPP, chat_id=sender, reply_to_message_id=msg.get("id")
        ),
        command=command,
        has_unsupported_media=unsupported and not parts,
    )


def parse_webhook(payload: dict[str, Any], salt: str) -> list[Inbound]:
    """Every user message in a (possibly batched) webhook. Status updates are ignored."""
    out: list[Inbound] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for msg in (change.get("value") or {}).get("messages", []) or []:
                inbound = _message_to_inbound(msg, salt)
                if inbound is not None:
                    out.append(inbound)
    return out


def split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        cut = cut if cut > limit // 2 else limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip("\n")
    return [*chunks, text] if text else chunks


class WhatsAppClient:
    """Messenger + MediaFetcher for the WhatsApp Cloud API."""

    def __init__(
        self, http: httpx.AsyncClient, token: str, phone_id: str, api_version: str = "v26.0"
    ) -> None:
        self._http = http
        self._auth = {"Authorization": f"Bearer {token}"}
        self._base = f"{GRAPH}/{api_version}"
        self._phone_id = phone_id

    async def _post(self, path: str, **kwargs: Any) -> dict[str, Any]:
        resp = await self._http.post(f"{self._base}/{path}", headers=self._auth, **kwargs)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def send_text(self, target: ReplyTarget, text: str) -> None:
        for i, chunk in enumerate(split_text(text)):
            body: dict[str, Any] = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": target.chat_id,
                "type": "text",
                "text": {"body": chunk, "preview_url": False},
            }
            if i == 0 and target.reply_to_message_id:
                body["context"] = {"message_id": target.reply_to_message_id}
            await self._post(f"{self._phone_id}/messages", json=body)

    async def send_typing(self, target: ReplyTarget) -> None:
        return None  # acknowledged with the "Checking…" text instead

    async def send_voice(self, target: ReplyTarget, ogg_path: str) -> None:
        data = Path(ogg_path).read_bytes()  # noqa: ASYNC240 - small local temp file
        media = await self._post(
            f"{self._phone_id}/media",
            data={"messaging_product": "whatsapp", "type": "audio/ogg"},
            files={"file": ("reply.ogg", data, "audio/ogg")},
        )
        await self._post(
            f"{self._phone_id}/messages",
            json={
                "messaging_product": "whatsapp",
                "to": target.chat_id,
                "type": "audio",
                "audio": {"id": media["id"]},
            },
        )

    async def fetch(self, part: Part, dest_dir: Path) -> Path:
        if not part.media_ref:
            raise ValueError("part has no media id")
        meta = await get_json(self._http, f"{self._base}/{part.media_ref}", headers=self._auth)
        if int(meta.get("file_size") or 0) > MAX_MEDIA_BYTES:
            raise ValueError("media too large")
        ext = {"image": ".jpg", "audio": ".ogg", "video": ".mp4"}.get(
            str(meta.get("mime_type", "")).split("/")[0], ".bin"
        )
        dest = dest_dir / f"{uuid4().hex}{ext}"
        async with self._http.stream("GET", meta["url"], headers=self._auth) as resp:
            resp.raise_for_status()
            size = 0
            with dest.open("wb") as f:  # noqa: ASYNC230 - streaming to a local temp file
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_MEDIA_BYTES:
                        raise ValueError("media too large")
                    f.write(chunk)
        return dest
