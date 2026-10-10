"""Liveness and readiness endpoints."""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from app import __version__
from app.core import db, queue
from app.core.config import Settings, get_settings

router = APIRouter(tags=["health"])

Checker = Callable[[], Awaitable[bool]]


class Health(BaseModel):
    status: str
    version: str


class Readiness(BaseModel):
    status: str
    checks: dict[str, bool]


def get_checkers(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, Checker]:
    return {
        "database": lambda: db.ping(settings.database_url),
        "redis": lambda: queue.ping(settings.redis_url),
    }


@router.get("/health", response_model=Health)
async def health() -> Health:
    return Health(status="ok", version=__version__)


@router.get("/health/ready", response_model=Readiness)
async def ready(
    response: Response,
    checkers: Annotated[dict[str, Checker], Depends(get_checkers)],
) -> Readiness:
    checks = {name: await check() for name, check in checkers.items()}
    ok = all(checks.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(status="ok" if ok else "degraded", checks=checks)
