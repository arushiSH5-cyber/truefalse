"""Builds real dependencies from settings. Tests build fakes instead (tests/fakes.py)."""

import importlib.util
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from app.core.config import Settings
from app.core.db import get_engine
from app.core.llm import AnthropicLLM, LLMClient, StructuredLLM
from app.core.logging import get_logger
from app.core.models import Platform
from app.matching.factcheck_index import FactCheckIndex, PgFactCheckIndex
from app.modality.hashes import PgImageHashStore
from app.modality.links import YtDlpDownloader
from app.modality.process import MediaFetcher, MediaProviders
from app.pipeline import Deps, PipelineConfig
from app.research.sources.base import Source
from app.research.sources.factcheck_api import GoogleFactCheckSource
from app.research.sources.news import BraveNewsSource, GdeltNewsSource
from app.research.sources.pubmed import PubMedSource
from app.research.sources.semantic_scholar import SemanticScholarSource
from app.research.sources.web_search import BraveWebSearchSource
from app.verify.judges import MIN_VALID_JUDGES, PANEL, JudgeSpec, panel_for

if TYPE_CHECKING:
    from app.ai_detect.detector import AIDetector
    from app.core.jobs import JobStore
    from app.ingestion.base import Messenger
    from app.language.detect import LanguageDetector
    from app.worker import Extras

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
        sources.append(BraveNewsSource(http, key))
    if settings.gdelt_enabled:
        sources.append(GdeltNewsSource(http))
    log.info("sources_enabled", sources=[s.kind.value for s in sources])
    return sources


def _judge_llm(
    settings: Settings, main: AnthropicLLM, provider: str, model: str
) -> StructuredLLM | None:
    """One judge's client, or None (logged) if its key or SDK extra is missing."""
    if provider == "anthropic":
        return main if model == main.model else main.with_model(model)
    key_name, extra, module = {
        "openai": ("OPENAI_API_KEY", "openai", "openai"),
        "google": ("GEMINI_API_KEY", "gemini", "google.genai"),
    }[provider]
    key = _secret(getattr(settings, key_name.lower()))
    if not key:
        log.warning("judge_skipped", model=f"{provider}:{model}", reason=f"{key_name} not set")
        return None
    if not importlib.util.find_spec(module.split(".")[0]):
        log.warning("judge_skipped", model=f"{provider}:{model}", reason=f"needs --extra {extra}")
        return None
    from app.core.providers import GeminiLLM, OpenAILLM

    if provider == "openai":
        return OpenAILLM(
            key,
            model,
            max_retries=settings.http_max_retries,
            send_reasoning_effort=settings.openai_reasoning_effort,
        )
    return GeminiLLM(key, model)


def build_panel(settings: Settings, main: AnthropicLLM) -> tuple[JudgeSpec, ...]:
    from app.core.providers import parse_model_specs

    judges: list[tuple[str, StructuredLLM]] = []
    for provider, model in parse_model_specs(settings.judge_models):
        llm = _judge_llm(settings, main, provider, model)
        if llm is not None:
            judges.append((f"{provider}:{model}", llm))
    if len(judges) < MIN_VALID_JUDGES:
        log.warning("judge_panel_single_model", model=main.model)
        return tuple(
            JudgeSpec(s.name, s.prompt, s.effort, main, f"anthropic:{main.model}") for s in PANEL
        )
    log.info("judge_panel", judges=[label for label, _ in judges])
    return panel_for(judges)


def build_ai(settings: Settings, http: httpx.AsyncClient) -> "AIDetector | None":
    if not settings.ai_detect_enabled:
        return None
    from app.ai_detect.classifiers import (
        HFAudioClassifier,
        HFImageClassifier,
        HiveClassifier,
        ScoreClassifier,
        SightengineClassifier,
    )
    from app.ai_detect.credentials import ContentCredentialsCheck
    from app.ai_detect.detector import AIDetector
    from app.ai_detect.reverse_image import ReverseImageSearch

    frames: list[ScoreClassifier] = []
    secret = _secret(settings.sightengine_api_secret)
    if settings.sightengine_api_user and secret:
        frames.append(SightengineClassifier(http, settings.sightengine_api_user, secret))
    if key := _secret(settings.hive_api_key):
        frames.append(HiveClassifier(http, key))
    local_ml = importlib.util.find_spec("transformers") is not None
    if settings.ai_image_model and local_ml:
        frames.append(HFImageClassifier(settings.ai_image_model))
    voice = (
        HFAudioClassifier(settings.ai_voice_model) if settings.ai_voice_model and local_ml else None
    )
    vision_key = _secret(settings.google_vision_api_key)
    detector = AIDetector(
        credentials=ContentCredentialsCheck() if importlib.util.find_spec("c2pa") else None,
        frame_classifiers=frames,
        voice_classifier=voice,
        reverse_search=ReverseImageSearch(http, vision_key) if vision_key else None,
        text_signal=settings.ai_text_signal,
    )
    log.info(
        "ai_detect_enabled",
        c2pa=detector.credentials is not None,
        classifiers=[c.name for c in frames],
        voice=voice is not None,
        reverse_image=detector.reverse_search is not None,
        ai_text=settings.ai_text_signal,
    )
    return detector


def build_index(settings: Settings) -> FactCheckIndex | None:
    if not settings.factcheck_index_enabled:
        return None
    from app.matching.embeddings import SentenceTransformerEmbedder

    return PgFactCheckIndex(
        get_engine(settings.database_url), SentenceTransformerEmbedder(settings.embedding_model)
    )


def build_platforms(
    settings: Settings, http: httpx.AsyncClient, jobs: "JobStore"
) -> tuple[dict[Platform, "Messenger"], dict[Platform, MediaFetcher]]:
    """Messengers and media fetchers for every configured platform (web is always on)."""
    from app.core.jobs import WebMessenger, WebUploadFetcher
    from app.ingestion.telegram import TelegramMediaFetcher, TelegramMessenger
    from app.ingestion.whatsapp import WhatsAppClient

    messengers: dict[Platform, Messenger] = {Platform.WEB: WebMessenger(jobs)}
    fetchers: dict[Platform, MediaFetcher] = {Platform.WEB: WebUploadFetcher(jobs)}
    if token := _secret(settings.telegram_bot_token):
        tg = TelegramMessenger(token, settings.http_timeout_seconds)
        messengers[Platform.TELEGRAM] = tg
        fetchers[Platform.TELEGRAM] = TelegramMediaFetcher(tg)
    wa_token = _secret(settings.whatsapp_token)
    if wa_token and settings.whatsapp_phone_id:
        wa = WhatsAppClient(
            http, wa_token, settings.whatsapp_phone_id, settings.whatsapp_api_version
        )
        messengers[Platform.WHATSAPP] = wa
        fetchers[Platform.WHATSAPP] = wa
    return messengers, fetchers


def build_media(
    settings: Settings,
    llm: LLMClient,
    http: httpx.AsyncClient,
    fetcher: MediaFetcher | None,
    fetchers: dict[Platform, MediaFetcher] | None = None,
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
        llm=llm,
        fetcher=fetcher,
        stt=stt,
        hashes=hashes,
        http=http,
        downloader=downloader,
        ai=build_ai(settings, http),
    )


def build_deps(
    settings: Settings,
    http: httpx.AsyncClient,
    fetcher: MediaFetcher | None = None,
    fetchers: dict[Platform, MediaFetcher] | None = None,
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
        media=build_media(settings, llm, http, fetcher, fetchers),
        lid=build_lid(settings),
        panel=build_panel(settings, llm),
        ai=build_ai(settings, http) if settings.ai_text_signal else None,
    )


def build_extras(settings: Settings, llm: LLMClient) -> "Extras":
    from app.core.prefs import RedisPrefsStore
    from app.respond.translate import Translator
    from app.worker import Extras

    tts = supports = None
    if settings.tts_enabled and importlib.util.find_spec("gtts"):
        from app.respond.tts import GTTSVoice

        voice = GTTSVoice(Path(tempfile.gettempdir()))
        tts, supports = voice, voice.supports
    return Extras(
        prefs=RedisPrefsStore(settings.redis_url),
        translator=Translator(llm),
        tts=tts,
        tts_supports=supports,
    )


def build_lid(settings: Settings) -> "LanguageDetector | None":
    if not settings.lid_model_path or not importlib.util.find_spec("fasttext"):
        return None
    from app.language.detect import FastTextDetector

    return FastTextDetector(settings.lid_model_path)
