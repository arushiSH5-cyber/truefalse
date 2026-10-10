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

    # Research sources
    google_factcheck_api_key: SecretStr | None = None
    semantic_scholar_api_key: SecretStr | None = None
    ncbi_api_key: SecretStr | None = None
    search_provider: Literal["brave", "none"] = "brave"
    search_api_key: SecretStr | None = None

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
