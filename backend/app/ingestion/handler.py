"""Platform-independent handling of an inbound message: ack fast, then queue the check."""

from app.core.logging import get_logger
from app.core.prefs import PrefsStore, UserPrefs
from app.ingestion.base import Inbound, JobQueue, Messenger
from app.ingestion.common import CHECKING, TEXT_ONLY, WELCOME, say
from app.language.names import autonym, parse_language_name
from app.respond.translate import Translator

log = get_logger(__name__)

CHECK_JOB = "check_submission"

LANGUAGE_SET = "✅ {name} ✔"
VOICE_ON = "🔊 Voice replies: ON. Send /voice again to turn them off."
VOICE_OFF = "🔇 Voice replies: OFF. Send /voice to turn them on."
LANGUAGE_AUTO = (
    "🌐 I'll reply in the language you write in. "
    "Send a language name (e.g. Español, हिंदी, العربية) to choose one."
)


async def handle_inbound(
    inbound: Inbound,
    messenger: Messenger,
    queue: JobQueue,
    prefs_store: PrefsStore | None = None,
    translator: Translator | None = None,
) -> str:
    """Returns what happened, for logs and tests."""
    user = inbound.submission.user_hash
    prefs = (await prefs_store.get(user) if prefs_store else None) or UserPrefs(user_hash=user)
    hint = prefs.reply_language or inbound.submission.language_hint

    async def reply(message: str | dict[str, str]) -> None:
        await messenger.send_text(inbound.target, await say(message, hint, translator))

    if inbound.command in ("start", "help"):
        await reply(WELCOME)
        if prefs_store and not prefs.welcomed:
            prefs.welcomed = True
            await prefs_store.save(prefs)
        return "welcome"
    if inbound.command == "voice" and prefs_store:
        prefs.voice_replies = not prefs.voice_replies
        await prefs_store.save(prefs)
        await reply(VOICE_ON if prefs.voice_replies else VOICE_OFF)
        return "voice_toggled"
    if inbound.command == "language" and prefs_store:
        prefs.reply_language = None
        await prefs_store.save(prefs)
        await reply(LANGUAGE_AUTO)
        return "language_auto"
    if inbound.command is not None:
        return "ignored_command"
    if inbound.has_unsupported_media:
        await reply(TEXT_ONLY)
        return "unsupported_media"
    if not inbound.submission.parts:
        return "empty"

    # Language switch: the whole message is just a language name, in any language.
    texts = [p.text for p in inbound.submission.parts if p.text]
    if prefs_store and len(inbound.submission.parts) == 1 and texts:
        code = parse_language_name(texts[0])
        if code:
            prefs.reply_language = code
            await prefs_store.save(prefs)
            await messenger.send_text(inbound.target, LANGUAGE_SET.format(name=autonym(code)))
            return "language_set"

    await reply(CHECKING)
    await queue.enqueue(
        CHECK_JOB,
        {
            "submission": inbound.submission.model_dump(mode="json"),
            "target": inbound.target.model_dump(mode="json"),
        },
    )
    log.info("queued", submission_id=inbound.submission.id, platform=inbound.target.platform)
    return "queued"
