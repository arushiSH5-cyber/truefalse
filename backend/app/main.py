"""FastAPI entrypoint."""

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import health, telegram, web, whatsapp
from app.core import db
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger, new_trace_id, trace_id_var

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    log.info("startup", env=settings.app_env, model=settings.anthropic_model)
    yield
    await db.dispose()
    log.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(title="TrueFalse", version=__version__, lifespan=lifespan)

    @app.middleware("http")
    async def trace_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        trace_id = request.headers.get("x-request-id") or new_trace_id()
        token = trace_id_var.set(trace_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            trace_id_var.reset(token)
        response.headers["x-request-id"] = trace_id
        log.info(
            "request",
            trace_id=trace_id,
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            latency_ms=round((time.perf_counter() - start) * 1000, 1),
        )
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in get_settings().web_origins.split(",") if o.strip()],
        allow_methods=["GET", "POST"],
        allow_headers=["X-Client-Id", "Content-Type", "Accept-Language"],
    )
    app.include_router(health.router)
    app.include_router(telegram.router)
    app.include_router(whatsapp.router)
    app.include_router(web.router)
    return app


app = create_app()
