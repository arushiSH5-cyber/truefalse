"""arq background worker. Run with: arq app.worker.WorkerSettings"""

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from app.core.config import get_settings
from app.core.container import build_deps, build_extras, build_http_client, build_platforms
from app.core.jobs import RedisJobStore
from app.core.logging import configure_logging, get_logger, trace_id_var
from app.core.models import CheckReport, PartKind, ReplyTarget, Submission
from app.core.prefs import PrefsStore, UserPrefs
from app.core.queue import redis_settings
from app.core.trace import RunTrace
from app.ingestion.base import Messenger
from app.ingestion.common import FAILURE, WELCOME, WORKING_ON_MEDIA, localized
from app.ingestion.router import RoutingMessenger
from app.modality.base import TextToSpeech
from app.pipeline import Deps, run_check
from app.respond.translate import Translator

log = get_logger(__name__)


@dataclass
class Extras:
    """Optional per-user features; all may be None (tests, minimal deployments)."""

    prefs: PrefsStore | None = None
    translator: Translator | None = None
    tts: TextToSpeech | None = None
    tts_supports: Callable[[str], bool] | None = None


async def _maybe_voice(
    extras: Extras, messenger: Messenger, target: ReplyTarget, report: CheckReport
) -> bool:
    if extras.tts is None or not report.spoken_text:
        return False
    if extras.tts_supports and not extras.tts_supports(report.reply_language):
        return False
    try:
        path = await extras.tts.synthesize(report.spoken_text, report.reply_language)
        try:
            await messenger.send_voice(target, path)
        finally:
            await asyncio.to_thread(Path(path).unlink, missing_ok=True)
        return True
    except Exception as exc:
        log.warning("voice_reply_failed", error=str(exc))
        return False


async def process(
    payload: dict[str, Any],
    deps: Deps,
    messenger: Messenger,
    prices: tuple[float, float],
    extras: Extras | None = None,
) -> str:
    """Run one check and reply. Never raises: on failure the user gets a polite message."""
    extras = extras or Extras()
    submission = Submission.model_validate(payload["submission"])
    target = ReplyTarget.model_validate(payload["target"])
    prefs: UserPrefs | None = None
    if extras.prefs:
        with contextlib.suppress(Exception):
            prefs = await extras.prefs.get(submission.user_hash)
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
        report = await run_check(
            submission,
            deps,
            trace,
            progress,
            reply_language=prefs.reply_language if prefs else None,
        )
        text = report.reply_text
        status = "ok"
    except Exception as exc:
        log.exception("check_failed", error=str(exc))
        report, text, status = None, FAILURE, "failed"

    # First-time users get the welcome, in their language, before their first answer.
    if report and extras.prefs and (prefs is None or not prefs.welcomed):
        welcome = WELCOME
        if extras.translator:
            welcome = await extras.translator.translate(WELCOME, report.reply_language)
        with contextlib.suppress(Exception):
            await messenger.send_text(target, welcome)
            prefs = prefs or UserPrefs(user_hash=submission.user_hash)
            prefs.welcomed = True
            await extras.prefs.save(prefs)

    try:
        await messenger.send_text(target, text)
    except Exception as exc:
        log.error("send_failed", error=str(exc))
        status = "send_failed"

    finish = getattr(messenger, "finish", None)  # web jobs store the structured result
    if finish is not None:
        with contextlib.suppress(Exception):
            await finish(target, report)

    # Voice in -> voice out; or always, if the user turned voice replies on.
    voice_sent = False
    sent_voice = any(p.kind is PartKind.AUDIO for p in submission.parts)
    if report and status == "ok" and (sent_voice or (prefs and prefs.voice_replies)):
        voice_sent = await _maybe_voice(extras, messenger, target, report)

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
        needs_human_review=report.needs_human_review if report else False,
        review_reasons=report.review_reasons if report else [],
        voters=sorted({v for d in report.decisions for v in d.voters}) if report else [],
        ai_overall=report.ai_report.overall if report else None,
        ai_detectors=sorted({s.detector for s in report.ai_report.signals}) if report else [],
        stages={k: v.model_dump() for k, v in trace.stages.items()},
        cost_usd=trace.cost_usd(*prices),
        voice_reply=voice_sent,
    )
    return status


async def check_submission(ctx: dict[str, Any], payload: dict[str, Any]) -> str:
    return await process(payload, ctx["deps"], ctx["messenger"], ctx["prices"], ctx["extras"])


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    ctx["http"] = build_http_client(settings)
    ctx["jobs"] = RedisJobStore(settings.redis_url)
    messengers, fetchers = build_platforms(settings, ctx["http"], ctx["jobs"])
    ctx["messenger"] = RoutingMessenger(messengers)
    ctx["deps"] = build_deps(settings, ctx["http"], fetchers=fetchers)
    ctx["prices"] = (settings.llm_input_price_per_mtok, settings.llm_output_price_per_mtok)
    ctx["extras"] = build_extras(settings, ctx["deps"].llm)
    log.info(
        "worker_startup", model=settings.anthropic_model, platforms=[p.value for p in messengers]
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    closables = [ctx["messenger"], ctx["jobs"], ctx["extras"].prefs]
    await asyncio.gather(
        ctx["http"].aclose(),
        *(c.close() for c in closables if hasattr(c, "close")),
        return_exceptions=True,
    )


class WorkerSettings:
    functions: ClassVar[list[Any]] = [check_submission]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings(get_settings().redis_url)
    job_timeout = 300
    max_tries = 1  # a retry would double-reply; failures send a polite message instead
