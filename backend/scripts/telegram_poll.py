"""Local dev runner: long-polls Telegram and runs checks in-process (no webhook, Redis, or worker).

Usage (from backend/):  uv run python -m scripts.telegram_poll
Requires ANTHROPIC_API_KEY and TELEGRAM_BOT_TOKEN in .env. Removes any webhook on the bot.
"""

import asyncio
from typing import Any

from telegram import Bot

from app.core.config import get_settings
from app.core.container import build_deps, build_http_client
from app.core.logging import configure_logging, get_logger
from app.ingestion.handler import handle_inbound
from app.ingestion.telegram import TelegramMessenger, parse_update
from app.worker import process

log = get_logger("telegram_poll")


class InlineQueue:
    """Runs jobs as background tasks in this process instead of via Redis."""

    def __init__(self, run: Any) -> None:
        self._run = run
        self._tasks: set[asyncio.Task[Any]] = set()

    async def enqueue(self, function: str, payload: dict[str, Any]) -> None:
        task = asyncio.create_task(self._run(payload))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.telegram_bot_token is None:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env")
    token = settings.telegram_bot_token.get_secret_value()
    salt = settings.user_hash_salt.get_secret_value()
    http = build_http_client(settings)
    deps = build_deps(settings, http)
    messenger = TelegramMessenger(token, settings.http_timeout_seconds)
    prices = (settings.llm_input_price_per_mtok, settings.llm_output_price_per_mtok)
    queue = InlineQueue(lambda payload: process(payload, deps, messenger, prices))

    async with Bot(token) as bot:
        await bot.delete_webhook()
        me = await bot.get_me()
        log.info("polling", bot=me.username)
        offset = None
        while True:
            updates = await bot.get_updates(offset=offset, timeout=30)
            for update in updates:
                offset = update.update_id + 1
                inbound = parse_update(update.to_dict(), salt)
                if inbound is not None:
                    await handle_inbound(inbound, messenger, queue)


if __name__ == "__main__":
    asyncio.run(main())
