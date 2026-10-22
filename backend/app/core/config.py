"""Application settings. Every secret comes from the environment (see .env.example)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    # Infrastructure
    database_url: str = "postgresql+asyncpg://truefalse:truefalse@localhost:5432/truefalse"
    redis_url: str = "redis://localhost:6379/0"

    # LLM
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-opus-5-5"
    # Judge panel: comma-separated provider:model, one judge each (2-3 recommended).
    # Providers: anthropic, openai, google. A judge whose key is missing is skipped; with
    # fewer than 2 usable judges the panel falls back to ANTHROPIC_MODEL with three prompts.
    judge_models: str = (
        "anthropic:claude-opus-5-5,anthropic:claude-sonnet-5-5,anthropic:claude-haiku-5-5"
    )
    openai_api_key: SecretStr | None = None
    # Send reasoning_effort to OpenAI (needs a reasoning model); turn off for other models
    openai_reasoning_effort: bool = True
    gemini_api_key: SecretStr | None = None
    # Server-side refusal fallback (reroutes a declined request to another model in-call)
    llm_use_fallbacks: bool = True
    # USD per million tokens, for cost metrics only
    llm_input_price_per_mtok: float = 4.0
    llm_output_price_per_mtok: float = 20.0

    # Messaging platforms
    telegram_bot_token: SecretStr | None = None
    telegram_webhook_secret: SecretStr | None = None
    whatsapp_token: SecretStr | None = None
    whatsapp_phone_id: str | None = None
    whatsapp_app_secret: SecretStr | None = None  # verifies X-Hub-Signature-256
    whatsapp_verify_token: SecretStr | None = None  # webhook GET handshake
    whatsapp_api_version: str = "v26.0"

    # Web app / PWA / extension (Phase 7)
    web_origins: str = "http://localhost:3000"  # comma-separated CORS origins
    web_checks_per_hour: int = Field(default=20, ge=1)  # per anonymous client
    share_card_font_dir: str = "/usr/share/fonts/truetype/noto"

    # Research sources
    google_factcheck_api_key: SecretStr | None = None
    semantic_scholar_api_key: SecretStr | None = None
    ncbi_api_key: SecretStr | None = None
    search_provider: Literal["brave", "none"] = "brave"
    search_api_key: SecretStr | None = None
    # GDELT news search (free, no key, many languages)
    gdelt_enabled: bool = True

    # Fact-check index (Phase 3)
    factcheck_index_enabled: bool = False
    embedding_model: str = "intfloat/multilingual-e5-base"
    # Cosine similarity (E5, normalized) above which an indexed fact-check counts as a match
    match_strong_threshold: float = Field(default=0.88, ge=0, le=1)

    # Media (Phase 5). Images and article links work out of the box; voice/video need the
    # `media` extra (faster-whisper, yt-dlp) and ffmpeg.
    media_enabled: bool = True
    whisper_model: str = "small"
    max_media_seconds: int = Field(default=3600, gt=0)
    image_hash_db: bool = False  # remember perceptual hashes in Postgres ("seen before")

    # Languages (Phase 6)
    lid_model_path: str | None = None  # fastText lid218e.bin / lid.176.bin; needs `lid` extra
    tts_enabled: bool = True  # voice replies via gTTS; needs `voice` extra + ffmpeg

    # Pipeline limits
    max_claims: int = Field(default=3, ge=1, le=10)
    research_max_tool_calls: int = Field(default=8, ge=0, le=30)
    research_time_budget_s: float = Field(default=60.0, gt=0)

    # Privacy: salt used when hashing user identifiers (phone numbers, chat ids)
    user_hash_salt: SecretStr = SecretStr("change-me")

    # Network defaults applied to every outbound call
    http_timeout_seconds: float = Field(default=10.0, gt=0)
    http_max_retries: int = Field(default=2, ge=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
