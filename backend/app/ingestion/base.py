"""Interfaces for outbound messaging and background jobs (fakes in tests/fakes.py)."""

from typing import Any, Protocol

from pydantic import BaseModel

from app.core.models import ReplyTarget, Submission


class Messenger(Protocol):
    async def send_text(self, target: ReplyTarget, text: str) -> None: ...

    async def send_typing(self, target: ReplyTarget) -> None: ...

    async def send_voice(self, target: ReplyTarget, ogg_path: str) -> None: ...


class JobQueue(Protocol):
    async def enqueue(self, function: str, payload: dict[str, Any]) -> None: ...


class Inbound(BaseModel):
    """A parsed inbound message plus how to reply. `command` is set for /start etc."""

    submission: Submission
    target: ReplyTarget
    command: str | None = None
    has_unsupported_media: bool = False
