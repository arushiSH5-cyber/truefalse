"""Local mode: API, worker and stores in one process. No Docker, Redis or Postgres.

    python -m app.local          (or ./run.sh from the repo root, which also starts the web app)

For demos and development: one machine, everything in memory, gone on restart. `.env` is read
again for every check, so a key added later works without a restart.
"""

import argparse
import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from app.api import health, telegram, web
from app.core.config import Settings, get_settings
from app.core.container import build_deps, build_platforms, build_translator
from app.core.jobs import RESULT_TTL_S, UPLOAD_TTL_S, InMemoryJobStore, JobRecord
from app.core.logging import get_logger
from app.core.models import Claim, Platform, ReplyTarget
from app.core.prefs import InMemoryPrefsStore
from app.core.ratelimit import InMemoryRateLimiter
from app.core.review import REVIEW_TTL_S, InMemoryReviewStore
from app.ingestion.router import RoutingMessenger
from app.main import create_app
from app.matching.verdict_cache import CachedVerdict, InMemoryVerdictCache
from app.worker import Extras, process

log = get_logger(__name__)

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
USER_AGENT = "Mozilla/5.0 (compatible; fact-check-client/1.0)"
CHECK_TIMEOUT_S = 300
MAX_WAITING = 10
DAILY_LIMIT = "TrueFalse has used today's free checks. Please try again tomorrow."
NO_KEY = "TrueFalse has no AI key yet. Run python3 scripts/set_key.py, then try again."


def load_settings() -> Settings:
    settings = Settings(_env_file=ENV_FILE)
    return settings.model_copy(
        update={
            # These need Redis or Postgres.
            "factcheck_index_enabled": False,
            "image_hash_db": False,
            "verdict_cache_enabled": False,
            "digest_enabled": False,
            # Free-tier quota is small: fewer searches per check.
            "research_max_tool_calls": min(settings.research_max_tool_calls, 5),
        }
    )


def engine_name(settings: Settings) -> str | None:
    for name, key in (
        ("anthropic", settings.anthropic_api_key),
        ("gemini", settings.gemini_api_key),
    ):
        if key and key.get_secret_value().strip():
            return name
    return None


class ExpiringJobStore(InMemoryJobStore):
    """The same retention the privacy note promises for Redis."""

    def __init__(self) -> None:
        super().__init__()
        self._job_born: dict[str, float] = {}
        self._upload_born: dict[str, float] = {}

    def prune(self) -> None:
        now = time.time()
        for born, items, ttl in (
            (self._job_born, self.jobs, RESULT_TTL_S),
            (self._upload_born, self.uploads, UPLOAD_TTL_S),
        ):
            for key in [k for k, t in born.items() if now - t > ttl]:
                del born[key]
                items.pop(key, None)

    async def create(self) -> JobRecord:
        self.prune()
        rec = await super().create()
        self._job_born[rec.id] = time.time()
        return rec

    async def put_upload(self, data: bytes, filename: str) -> str:
        key = await super().put_upload(data, filename)
        self._upload_born[key] = time.time()
        return key


class ExpiringVerdictCache(InMemoryVerdictCache):
    def __init__(self, ttl_days: int) -> None:
        super().__init__()
        self._ttl_s = ttl_days * 86400

    async def get(self, claim: Claim) -> CachedVerdict | None:
        entry = await super().get(claim)
        if entry and time.time() - entry.cached_at.timestamp() < self._ttl_s:
            return entry
        return None


class LocalRunner:
    """JobQueue that runs each check in this process, two at a time."""

    def __init__(
        self, http: httpx.AsyncClient, jobs: ExpiringJobStore, daily: "DailyLimit | None" = None
    ) -> None:
        self.http = http
        self.daily = daily
        self.jobs = jobs
        self.reviews = InMemoryReviewStore()
        self.verdicts = ExpiringVerdictCache(load_settings().verdict_cache_ttl_days)
        self._slots = asyncio.Semaphore(2)
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def full(self) -> bool:
        return len(self._tasks) >= MAX_WAITING

    async def enqueue(self, function: str, payload: dict[str, Any]) -> None:
        task = asyncio.create_task(self._run(payload))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, payload: dict[str, Any]) -> None:
        async with self._slots:
            try:
                settings = load_settings()
                messengers, fetchers = build_platforms(settings, self.http, self.jobs)
                deps = build_deps(settings, self.http, fetchers=fetchers)
                deps.verdicts = self.verdicts
                prices = (settings.llm_input_price_per_mtok, settings.llm_output_price_per_mtok)
                target = ReplyTarget.model_validate(payload["target"])
                # Web checks are counted when they arrive; chat-app checks are counted here.
                if (
                    target.platform is not Platform.WEB
                    and self.daily
                    and not self.daily.take(settings.daily_check_limit)
                ):
                    await RoutingMessenger(messengers).send_text(target, DAILY_LIMIT)
                    return
                async with asyncio.timeout(CHECK_TIMEOUT_S):
                    await process(
                        payload,
                        deps,
                        RoutingMessenger(messengers),
                        prices,
                        Extras(reviews=self.reviews),
                    )
            except Exception as exc:
                log.error("local_check_failed", error=type(exc).__name__)
                target = ReplyTarget.model_validate(payload["target"])
                if target.platform is Platform.WEB and (rec := await self.jobs.get(target.chat_id)):
                    rec.status = "failed"
                    await self.jobs.update(rec)

    async def prune(self) -> None:
        while True:
            await asyncio.sleep(60)
            self.jobs.prune()
            cutoff = time.time() - REVIEW_TTL_S
            for key, item in list(self.reviews.items.items()):
                if item.created.timestamp() < cutoff:
                    del self.reviews.items[key]


class Status(BaseModel):
    ready: bool
    engine: str | None
    searches: list[str]  # names only, never key values


class DailyLimit:
    """All checks today, from anyone. A client id is free to invent; this count isn't."""

    def __init__(self) -> None:
        self._day = ""
        self._used = 0

    def take(self, limit: int) -> bool:
        today = datetime.now(UTC).date().isoformat()
        if today != self._day:
            self._day, self._used = today, 0
        if self._used >= limit:
            return False
        self._used += 1
        return True


def create_local_app(queue: Any = None) -> FastAPI:
    app = create_app()
    jobs = ExpiringJobStore()
    limiter = InMemoryRateLimiter()
    daily = DailyLimit()
    http = httpx.AsyncClient(timeout=10, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    runner = LocalRunner(http, jobs, daily)
    prefs = InMemoryPrefsStore()  # chat apps: language and voice choices, until restart
    queue = queue or runner  # tests pass a fake queue

    app.dependency_overrides.update(
        {
            get_settings: load_settings,
            telegram.get_queue: lambda: queue,
            telegram.get_reviews: lambda: runner.reviews,
            telegram.get_prefs: lambda: prefs,
            telegram.get_translator: lambda: build_translator(load_settings()),
            web.get_jobs: lambda: jobs,
            web.get_limiter: lambda: limiter,
            health.get_checkers: lambda: {},
        }
    )

    @app.middleware("http")
    async def refuse_early(request: Request, call_next: Any) -> Response:
        if request.method == "POST" and request.url.path == "/api/check":
            problem = None
            settings = load_settings()
            if engine_name(settings) is None:
                problem = HTTPException(503, NO_KEY)
            elif queue.full:
                problem = HTTPException(503, "TrueFalse is busy. Try again in a minute.")
            elif not daily.take(settings.daily_check_limit):
                problem = HTTPException(503, DAILY_LIMIT)
            if problem:
                return Response(
                    f'{{"detail":"{problem.detail}"}}',
                    status_code=problem.status_code,
                    media_type="application/json",
                )
        response: Response = await call_next(request)
        return response

    @app.get("/api/status")
    async def status(settings: Annotated[Settings, Depends(get_settings)]) -> Status:
        searches = ["PubMed", "Semantic Scholar", "Wikipedia"]
        if settings.gdelt_enabled:
            searches.append("GDELT news")
        if (
            settings.google_factcheck_api_key
            and settings.google_factcheck_api_key.get_secret_value()
        ):
            searches.append("Google Fact Check")
        engine = engine_name(settings)
        return Status(ready=engine is not None, engine=engine, searches=searches)

    base_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(inner: FastAPI) -> AsyncIterator[None]:
        async with base_lifespan(inner):
            pruner = asyncio.create_task(runner.prune())
            yield
            pruner.cancel()
            await http.aclose()

    app.router.lifespan_context = lifespan
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    # Loopback by default: phones reach it through the web app's /backend proxy. A hosted
    # checker (Render) passes --host 0.0.0.0 so the web app on GitHub Pages can call it.
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    uvicorn.run(create_local_app(), host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
