"""Builds real dependencies from settings. Tests build fakes instead (tests/fakes.py)."""

import importlib.util

import httpx

from app.core.config import Settings
from app.core.db import get_engine
from app.core.llm import AnthropicLLM, LLMClient
from app.core.logging import get_logger
from app.matching.factcheck_index import FactCheckIndex, PgFactCheckIndex
from app.modality.hashes import PgImageHashStore
from app.modality.links import YtDlpDownloader
from app.modality.process import MediaFetcher, MediaProviders
from app.pipeline import Deps, PipelineConfig
from app.research.sources.base import Source
from app.research.sources.factcheck_api import GoogleFactCheckSource
from app.research.sources.pubmed import PubMedSource
from app.research.sources.semantic_scholar import SemanticScholarSource
from app.research.sources.web_search import BraveWebSearchSource

log = get_logger(__name__)


class ConfigError(Exception):
    pass


def _secret(value: object) -> str | None:
    get = getattr(value, "get_secret_value", None)
    return get() if get else None


def build_http_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.http_timeout_seconds),
        headers={"User-Agent": "TrueFalse/0.1 (misinformation checker; research use)"},
        follow_redirects=True,
    )


def build_sources(settings: Settings, http: httpx.AsyncClient) -> list[Source]:
    sources: list[Source] = []
    if key := _secret(settings.google_factcheck_api_key):
        sources.append(GoogleFactCheckSource(http, key))
    # Semantic Scholar and PubMed work without a key (lower rate limits).
    sources.append(SemanticScholarSource(http, _secret(settings.semantic_scholar_api_key)))
    sources.append(PubMedSource(http, _secret(settings.ncbi_api_key)))
    if settings.search_provider == "brave" and (key := _secret(settings.search_api_key)):
        sources.append(BraveWebSearchSource(http, key))
    log.info("sources_enabled", sources=[s.kind.value for s in sources])
    return sources


def build_index(settings: Settings) -> FactCheckIndex | None:
    if not settings.factcheck_index_enabled:
        return None
    from app.matching.embeddings import SentenceTransformerEmbedder

    return PgFactCheckIndex(
        get_engine(settings.database_url), SentenceTransformerEmbedder(settings.embedding_model)
    )


def build_media(
    settings: Settings, llm: LLMClient, http: httpx.AsyncClient, fetcher: MediaFetcher | None
) -> MediaProviders | None:
    if not settings.media_enabled:
        return None
    stt = downloader = None
    if importlib.util.find_spec("faster_whisper"):
        from app.modality.audio import FasterWhisperSTT

        stt = FasterWhisperSTT(settings.whisper_model)
    if importlib.util.find_spec("yt_dlp"):
        downloader = YtDlpDownloader(max_duration_s=settings.max_media_seconds)
    hashes = PgImageHashStore(get_engine(settings.database_url)) if settings.image_hash_db else None
    log.info(
        "media_enabled",
        stt=stt is not None,
        downloader=downloader is not None,
        image_hashes=hashes is not None,
    )
    return MediaProviders(
        llm=llm, fetcher=fetcher, stt=stt, hashes=hashes, http=http, downloader=downloader
    )


def build_deps(
    settings: Settings, http: httpx.AsyncClient, fetcher: MediaFetcher | None = None
) -> Deps:
    api_key = _secret(settings.anthropic_api_key)
    if not api_key:
        raise ConfigError("ANTHROPIC_API_KEY is not set")
    llm = AnthropicLLM(
        api_key,
        settings.anthropic_model,
        max_retries=settings.http_max_retries,
        use_fallbacks=settings.llm_use_fallbacks,
    )
    return Deps(
        llm=llm,
        sources=build_sources(settings, http),
        config=PipelineConfig(
            max_claims=settings.max_claims,
            research_max_tool_calls=settings.research_max_tool_calls,
            research_time_budget_s=settings.research_time_budget_s,
            match_strong_threshold=settings.match_strong_threshold,
        ),
        index=build_index(settings),
        media=build_media(settings, llm, http, fetcher),
    )
