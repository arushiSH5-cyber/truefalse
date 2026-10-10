"""arq background worker. Run with: arq app.worker.WorkerSettings"""

import asyncio
import contextlib
from typing import Any, ClassVar

from app.core.config import get_settings
from app.core.container import build_deps, build_http_client
from app.core.logging import configure_logging, get_logger, trace_id_var
from app.core.models import ReplyTarget, Submission
from app.core.queue import redis_settings
from app.core.trace import RunTrace
from app.ingestion.base import Messenger
from app.ingestion.common import FAILURE, WORKING_ON_MEDIA, localized
from app.ingestion.telegram import TelegramMediaFetcher, TelegramMessenger
from app.pipeline import Deps, run_check

log = get_logger(__name__)


async def process(
    payload: dict[str, Any], deps: Deps, messenger: Messenger, prices: tuple[float, float]
) -> str:
    """Run one check and reply. Never raises: on failure the user gets a polite message."""
    submission = Submission.model_validate(payload["submission"])
    target = ReplyTarget.model_validate(payload["target"])
    trace_id_var.set(submission.id)
    trace = RunTrace()
    with contextlib.suppress(Exception):
        await messenger.send_typing(target)

    async def progress(kind: str) -> None:
        if kind == "media":
            with contextlib.suppress(Exception):
                await messenger.send_text(
                    target, localized(WORKING_ON_MEDIA, submission.language_hint)
                )

    try:
        report = await run_check(submission, deps, trace, progress)
        text = report.reply_text
        status = "ok"
    except Exception as exc:
        log.exception("check_failed", error=str(exc))
        report, text, status = None, FAILURE, "failed"
    try:
        await messenger.send_text(target, text)
    except Exception as exc:
        log.error("send_failed", error=str(exc))
        status = "send_failed"

    # Anonymized metrics only: no message text, no user identifiers.
    log.info(
        "check_completed",
        status=status,
        platform=submission.platform,
        language=report.language if report else None,
        verdict=report.overall if report else None,
        confidence=report.overall_confidence if report else None,
        n_claims=len(report.claims) if report else 0,
        n_evidence=len(report.evidence) if report else 0,
        agreement=[d.agreement for d in report.decisions] if report else [],
        probability=[d.probability for d in report.decisions] if report else [],
        grounded_ratio=[d.grounded_ratio for d in report.decisions] if report else [],
        judges_disagree=any(d.judges_disagree for d in report.decisions) if report else False,
        stages={k: v.model_dump() for k, v in trace.stages.items()},
        cost_usd=trace.cost_usd(*prices),
    )
    return status


async def check_submission(ctx: dict[str, Any], payload: dict[str, Any]) -> str:
    return await process(payload, ctx["deps"], ctx["messenger"], ctx["prices"])


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    token = settings.telegram_bot_token
    if token is None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    ctx["http"] = build_http_client(settings)
    ctx["messenger"] = TelegramMessenger(token.get_secret_value(), settings.http_timeout_seconds)
    ctx["deps"] = build_deps(settings, ctx["http"], fetcher=TelegramMediaFetcher(ctx["messenger"]))
    ctx["prices"] = (settings.llm_input_price_per_mtok, settings.llm_output_price_per_mtok)
    log.info("worker_startup", model=settings.anthropic_model)


async def shutdown(ctx: dict[str, Any]) -> None:
    await asyncio.gather(ctx["messenger"].close(), ctx["http"].aclose(), return_exceptions=True)


class WorkerSettings:
    functions: ClassVar[list[Any]] = [check_submission]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings(get_settings().redis_url)
    job_timeout = 300
    max_tries = 1  # a retry would double-reply; failures send a polite message instead
