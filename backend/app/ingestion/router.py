"""Routes outbound messages to the right platform client (Telegram, WhatsApp, web)."""

from app.core.models import CheckReport, Platform, ReplyTarget
from app.ingestion.base import Messenger


class RoutingMessenger:
    def __init__(self, by_platform: dict[Platform, Messenger]) -> None:
        self._by = by_platform

    def _for(self, target: ReplyTarget) -> Messenger:
        try:
            return self._by[target.platform]
        except KeyError:
            raise RuntimeError(f"no messenger configured for {target.platform}") from None

    async def send_text(self, target: ReplyTarget, text: str) -> None:
        await self._for(target).send_text(target, text)

    async def send_typing(self, target: ReplyTarget) -> None:
        await self._for(target).send_typing(target)

    async def send_voice(self, target: ReplyTarget, ogg_path: str) -> None:
        await self._for(target).send_voice(target, ogg_path)

    async def finish(self, target: ReplyTarget, report: CheckReport | None) -> None:
        finish = getattr(self._for(target), "finish", None)
        if finish is not None:
            await finish(target, report)

    async def close(self) -> None:
        for m in self._by.values():
            close = getattr(m, "close", None)
            if close is not None:
                await close()
