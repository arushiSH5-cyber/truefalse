"""Telegram adapter: webhook update -> Submission, and a Messenger that sends replies."""

from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.core.models import Part, PartKind, Platform, ReplyTarget, Submission
from app.ingestion.base import Inbound
from app.ingestion.common import hash_user


class _TgUser(BaseModel):
    id: int
    language_code: str | None = None


class _TgChat(BaseModel):
    id: int
    type: str


class _TgMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message_id: int
    chat: _TgChat
    from_: _TgUser | None = Field(default=None, alias="from")
    text: str | None = None
    caption: str | None = None
    photo: list[dict[str, Any]] | None = None
    voice: dict[str, Any] | None = None
    audio: dict[str, Any] | None = None
    video: dict[str, Any] | None = None
    video_note: dict[str, Any] | None = None
    document: dict[str, Any] | None = None


class TgUpdate(BaseModel):
    update_id: int
    message: _TgMessage | None = None


_DOC_KINDS = {"image": PartKind.IMAGE, "video": PartKind.VIDEO, "audio": PartKind.AUDIO}

# Bot API getFile can only download files up to 20 MB.
TELEGRAM_MAX_DOWNLOAD = 20 * 1024 * 1024


class MediaTooLargeError(Exception):
    pass


def parse_update(data: dict[str, Any], salt: str) -> Inbound | None:
    update = TgUpdate.model_validate(data)
    msg = update.message
    if msg is None or msg.from_ is None:
        return None  # edits, channel posts, callbacks: ignored for now

    text = msg.text or msg.caption
    parts: list[Part] = []
    command = None
    if text:
        stripped = text.strip()
        if stripped.startswith("/"):
            command = stripped.split()[0][1:].split("@")[0].lower()
        else:
            parts.append(Part(kind=PartKind.TEXT, text=stripped))

    # Media is passed by Telegram file_id; the worker downloads it into a temp dir.
    if msg.photo:
        parts.append(Part(kind=PartKind.IMAGE, media_ref=msg.photo[-1].get("file_id")))
    for media, kind in (
        (msg.voice, PartKind.AUDIO),
        (msg.audio, PartKind.AUDIO),
        (msg.video, PartKind.VIDEO),
        (msg.video_note, PartKind.VIDEO),
    ):
        if media:
            parts.append(Part(kind=kind, media_ref=media.get("file_id")))
    unsupported_doc = False
    if msg.document:
        doc_kind = _DOC_KINDS.get(str(msg.document.get("mime_type", "")).split("/")[0])
        if doc_kind:
            parts.append(Part(kind=doc_kind, media_ref=msg.document.get("file_id")))
        else:
            unsupported_doc = True

    submission = Submission(
        platform=Platform.TELEGRAM,
        user_hash=hash_user(salt, "telegram", str(msg.from_.id)),
        language_hint=msg.from_.language_code,
        parts=parts,
    )
    target = ReplyTarget(
        platform=Platform.TELEGRAM,
        chat_id=str(msg.chat.id),
        reply_to_message_id=str(msg.message_id),
    )
    return Inbound(
        submission=submission,
        target=target,
        command=command,
        has_unsupported_media=unsupported_doc and not parts,
    )


class TelegramMessenger:
    """Sends messages with python-telegram-bot. Lazily initialized, reused across sends."""

    def __init__(self, token: str, timeout_s: float = 10.0) -> None:
        from telegram import Bot
        from telegram.request import HTTPXRequest

        request = HTTPXRequest(
            connect_timeout=timeout_s, read_timeout=timeout_s, write_timeout=timeout_s
        )
        self._bot = Bot(token, request=request)
        self._ready = False

    async def _ensure(self) -> None:
        if not self._ready:
            await self._bot.initialize()
            self._ready = True

    async def bot(self) -> Any:
        await self._ensure()
        return self._bot

    async def send_text(self, target: ReplyTarget, text: str) -> None:
        from telegram import LinkPreviewOptions, ReplyParameters

        await self._ensure()
        reply = (
            ReplyParameters(
                message_id=int(target.reply_to_message_id), allow_sending_without_reply=True
            )
            if target.reply_to_message_id
            else None
        )
        await self._bot.send_message(
            chat_id=int(target.chat_id),
            text=text,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            reply_parameters=reply,
        )

    async def send_typing(self, target: ReplyTarget) -> None:
        await self._ensure()
        await self._bot.send_chat_action(chat_id=int(target.chat_id), action="typing")

    async def close(self) -> None:
        if self._ready:
            await self._bot.shutdown()
            self._ready = False


class TelegramMediaFetcher:
    """Downloads Telegram media by file_id into the request's temp dir."""

    def __init__(self, messenger: TelegramMessenger) -> None:
        self._messenger = messenger

    async def fetch(self, part: Part, dest_dir: Path) -> Path:
        if not part.media_ref:
            raise ValueError("part has no media reference")
        bot = await self._messenger.bot()
        tg_file = await bot.get_file(part.media_ref)
        if tg_file.file_size and tg_file.file_size > TELEGRAM_MAX_DOWNLOAD:
            raise MediaTooLargeError(f"{tg_file.file_size} bytes")
        suffix = Path(tg_file.file_path or "").suffix or {
            PartKind.IMAGE: ".jpg",
            PartKind.AUDIO: ".ogg",
            PartKind.VIDEO: ".mp4",
        }.get(part.kind, ".bin")
        dest = dest_dir / f"{uuid4().hex}{suffix}"
        await tg_file.download_to_drive(custom_path=dest)
        return dest
