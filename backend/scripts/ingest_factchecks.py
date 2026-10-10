"""Load published fact-checks (schema.org ClaimReview) into the Postgres fact-check index.

Sources:
  --file PATH_OR_URL   A ClaimReview DataFeed / JSON-LD file, e.g. the Data Commons feed
                       (download link: https://datacommons.org/factcheck/download)
  --api QUERIES_FILE   Harvest via the Google Fact Check Tools API, one query per line
                       (needs GOOGLE_FACTCHECK_API_KEY); --languages es,hi,... repeats per language

Usage (from backend/, after `alembic upgrade head` and `uv sync --extra ml`):
  uv run python -m scripts.ingest_factchecks --file ~/Downloads/claimreview.json
  uv run python -m scripts.ingest_factchecks --api queries.txt --languages en,es,hi,ar
"""

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.container import build_http_client
from app.core.db import get_engine
from app.core.http import get_json
from app.core.logging import configure_logging, get_logger
from app.matching.claimreview import FactCheckRecord, dedupe, from_factcheck_api, parse_feed
from app.matching.embeddings import SentenceTransformerEmbedder
from app.matching.factcheck_index import PgFactCheckIndex
from app.research.sources.factcheck_api import URL as FACTCHECK_URL

log = get_logger("ingest")


def _read(path: str) -> str:
    return Path(path).expanduser().read_text(encoding="utf-8")


async def load_file(source: str, http: httpx.AsyncClient) -> list[FactCheckRecord]:
    if source.startswith(("http://", "https://")):
        resp = await http.get(source, timeout=300)
        resp.raise_for_status()
        data: Any = resp.json()
    else:
        raw = await asyncio.to_thread(_read, source)
        data = json.loads(raw)
    return list(parse_feed(data))


async def harvest_api(
    http: httpx.AsyncClient,
    key: str,
    queries: Sequence[str],
    languages: Sequence[str | None],
    max_pages: int,
) -> list[FactCheckRecord]:
    out: list[FactCheckRecord] = []
    for lang in languages:
        for query in queries:
            token = None
            for _ in range(max_pages):
                params: dict[str, Any] = {"query": query, "pageSize": 100, "key": key}
                if lang:
                    params["languageCode"] = lang
                if token:
                    params["pageToken"] = token
                data = await get_json(http, FACTCHECK_URL, params=params)
                out += list(from_factcheck_api(data))
                token = data.get("nextPageToken")
                if not token:
                    break
                await asyncio.sleep(0.2)  # stay well under API quotas
            log.info("harvested", query=query, language=lang, total=len(out))
    return out


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--file", action="append", default=[])
    parser.add_argument("--api", help="file with one search query per line")
    parser.add_argument("--languages", default="", help="comma-separated BCP-47 codes")
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0, help="cap records (0 = no cap)")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    records: list[FactCheckRecord] = []
    async with build_http_client(settings) as http:
        for source in args.file:
            records += await load_file(source, http)
        if args.api:
            key = settings.google_factcheck_api_key
            if key is None:
                raise SystemExit("GOOGLE_FACTCHECK_API_KEY is required for --api")
            lines = (await asyncio.to_thread(_read, args.api)).splitlines()
            queries = [q.strip() for q in lines if q.strip()]
            langs: list[str | None] = [x.strip() for x in args.languages.split(",") if x.strip()]
            records += await harvest_api(
                http, key.get_secret_value(), queries, langs or [None], args.max_pages
            )
    records = dedupe(records)
    if args.limit:
        records = records[: args.limit]
    log.info("loaded", records=len(records))

    index = PgFactCheckIndex(
        get_engine(settings.database_url), SentenceTransformerEmbedder(settings.embedding_model)
    )
    written = await index.upsert(records)
    log.info("ingest_done", written=written)


if __name__ == "__main__":
    asyncio.run(main())
