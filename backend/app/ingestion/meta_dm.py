"""Instagram DMs and Facebook Messenger: webhook parsing, sending replies, media download.

Both use Meta's Send API and the same webhook shape (entry[].messaging[]):
- Instagram (Instagram API with Instagram Login): graph.instagram.com, Instagram user token.
- Messenger: graph.facebook.com, Page access token.
Docs: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api
Signatures (X-Hub-Signature-256) are checked with the same helper as WhatsApp.
"""

from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from app.core.models import Part, PartKind, Platform, ReplyTarget, Submission
from app.ingestion.base import Inbound
from app.ingestion.common import hash_user
from app.ingestion.whatsapp import split_text

HOSTS = {
    Platform.INSTAGRAM: "https://graph.instagram.com",
    Platform.MESSENGER: "https://graph.facebook.com",
}
MAX_TEXT = {Platform.INSTAGRAM: 1000, Platform.MESSENGER: 2000}
MAX_MEDIA_BYTES = 25 * 1024 * 1024

# Attachment types that are a file we can download and read.
_MEDIA = {
    "image": PartKind.IMAGE,
    "sticker": PartKind.IMAGE,
    "video": PartKind.VIDEO,
    "ig_reel": PartKind.VIDEO,
    "reel": PartKind.VIDEO,
    "audio": PartKind.AUDIO,
}


def _event_to_inbound(event: dict[str, Any], salt: str, platform: Platform) -> Inbound | None:
    message = event.get("message")
    sender = (event.get("sender") or {}).get("id")
    if not message or not sender or message.get("is_echo") or message.get("is_deleted"):
        return None  # our own replies, reactions, read receipts, deleted messages
    parts: list[Part] = []
    command = None
    command_args = ""
    text = (message.get("text") or "").strip()
    if text.startswith("/"):
        head, _, command_args = text.partition(" ")
        command = head[1:].lower()
        command_args = command_args.strip()
    elif text:
        parts.append(Part(kind=PartKind.TEXT, text=text))

    unsupported = False
    for att in message.get("attachments") or []:
        kind = _MEDIA.get(str(att.get("type")))
        payload = att.get("payload") or {}
        url = payload.get("url")
        title = (payload.get("title") or "").strip()
        if title:
            parts.append(Part(kind=PartKind.TEXT, text=title))
        if kind and url:
            parts.append(Part(kind=kind, media_ref=url))
        elif url:
            # Shared posts and links: check the link like one pasted in the web app.
            parts.append(Part(kind=PartKind.TEXT, text=url))
        elif not title:
            unsupported = True

    return Inbound(
        submission=Submission(
            platform=platform,
            user_hash=hash_user(salt, platform.value, sender),
            language_hint=None,  # Meta does not share the user's app language
            parts=parts,
        ),
        target=ReplyTarget(
            platform=platform, chat_id=sender, reply_to_message_id=message.get("mid")
        ),
        command=command,
        command_args=command_args,
        has_unsupported_media=unsupported and not parts,
    )


def parse_dm_webhook(payload: dict[str, Any], salt: str, platform: Platform) -> list[Inbound]:
    """Every user message in a (possibly batched) Instagram or Messenger webhook."""
    out: list[Inbound] = []
    for entry in payload.get("entry", []) or []:
        for event in entry.get("messaging", []) or []:
            inbound = _event_to_inbound(event, salt, platform)
            if inbound is not None:
                out.append(inbound)
    return out


class MetaDMClient:
    """Messenger + MediaFetcher for Instagram DMs or Facebook Messenger."""

    def __init__(
        self, http: httpx.AsyncClient, token: str, platform: Platform, api_version: str = "v26.0"
    ) -> None:
        self._http = http
        self._auth = {"Authorization": f"Bearer {token}"}
        self._url = f"{HOSTS[platform]}/{api_version}/me/messages"
        self._limit = MAX_TEXT[platform]

    async def _send(self, body: dict[str, Any]) -> None:
        resp = await self._http.post(self._url, headers=self._auth, json=body)
        resp.raise_for_status()

    async def send_text(self, target: ReplyTarget, text: str) -> None:
        for chunk in split_text(text, self._limit):
            await self._send({"recipient": {"id": target.chat_id}, "message": {"text": chunk}})

    async def send_typing(self, target: ReplyTarget) -> None:
        return None  # acknowledged with the "Checking…" text instead

    async def send_voice(self, target: ReplyTarget, ogg_path: str) -> None:
        return None  # the Send API needs a public URL for audio; replies stay text

    async def fetch(self, part: Part, dest_dir: Path) -> Path:
        url = part.media_ref
        if not url or not url.startswith("https://"):
            raise ValueError("part has no media URL")
        ext = {PartKind.IMAGE: ".jpg", PartKind.AUDIO: ".mp4", PartKind.VIDEO: ".mp4"}.get(
            part.kind, ".bin"
        )
        dest = dest_dir / f"{uuid4().hex}{ext}"
        # Attachment URLs are signed CDN links: no token needed (and none is sent to the CDN).
        async with self._http.stream("GET", url) as resp:
            resp.raise_for_status()
            size = 0
            with dest.open("wb") as f:  # noqa: ASYNC230 - streaming to a local temp file
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_MEDIA_BYTES:
                        raise ValueError("media too large")
                    f.write(chunk)
        return dest
