"""arq background worker. Run with: arq app.worker.WorkerSettings"""

from typing import Any, ClassVar

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.queue import redis_settings

log = get_logger(__name__)


async def ping_job(_ctx: dict[str, Any]) -> str:
    """Smoke-test job; real pipeline jobs arrive in Phase 2."""
    log.info("ping_job")
    return "pong"


async def startup(_ctx: dict[str, Any]) -> None:
    configure_logging(get_settings().log_level)
    log.info("worker_startup")


class WorkerSettings:
    functions: ClassVar[list[Any]] = [ping_job]
    on_startup = startup
    redis_settings = redis_settings(get_settings().redis_url)
    job_timeout = 300
    max_tries = 3
