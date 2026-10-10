"""Outbound HTTP with timeouts and retries. Every external source call goes through here."""

import asyncio
from typing import Any

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class HttpError(Exception):
    pass


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    max_retries: int = 2,
    backoff_s: float = 0.5,
) -> Any:
    return (await _get(client, url, params, headers, max_retries, backoff_s)).json()


async def get_text(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    max_retries: int = 2,
    backoff_s: float = 0.5,
) -> str:
    return (await _get(client, url, params, headers, max_retries, backoff_s)).text


async def _get(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any] | None,
    headers: dict[str, str] | None,
    max_retries: int,
    backoff_s: float,
) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code not in RETRY_STATUS:
                resp.raise_for_status()
                return resp
            last_exc = HttpError(f"retryable status {resp.status_code}")
        except httpx.TransportError as exc:  # timeouts, connection errors
            last_exc = exc
        except httpx.HTTPStatusError as exc:
            raise HttpError(f"{exc.response.status_code} from {url}") from exc
        if attempt < max_retries:
            await asyncio.sleep(backoff_s * 2**attempt)
    log.warning("http_failed", url=url, error=str(last_exc))
    raise HttpError(f"giving up on {url}: {last_exc}") from last_exc
