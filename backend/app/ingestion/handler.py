"""Platform-independent handling of an inbound message: ack fast, then queue the check."""

from app.core.logging import get_logger
from app.ingestion.base import Inbound, JobQueue, Messenger
from app.ingestion.common import TEXT_ONLY, WELCOME, checking_text

log = get_logger(__name__)

CHECK_JOB = "check_submission"


async def handle_inbound(inbound: Inbound, messenger: Messenger, queue: JobQueue) -> str:
    """Returns what happened, for logs and tests."""
    if inbound.command in ("start", "help"):
        await messenger.send_text(inbound.target, WELCOME)
        return "welcome"
    if inbound.command is not None:
        return "ignored_command"
    if inbound.has_unsupported_media:
        await messenger.send_text(inbound.target, TEXT_ONLY)
        return "unsupported_media"
    if not inbound.submission.parts:
        return "empty"

    await messenger.send_text(inbound.target, checking_text(inbound.submission.language_hint))
    await queue.enqueue(
        CHECK_JOB,
        {
            "submission": inbound.submission.model_dump(mode="json"),
            "target": inbound.target.model_dump(mode="json"),
        },
    )
    log.info("queued", submission_id=inbound.submission.id, platform=inbound.target.platform)
    return "queued"
