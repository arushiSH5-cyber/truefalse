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

    # Messaging platforms
    telegram_bot_token: SecretStr | None = None
    whatsapp_token: SecretStr | None = None
    whatsapp_phone_id: str | None = None

    # Research sources
    google_factcheck_api_key: SecretStr | None = None
    semantic_scholar_api_key: SecretStr | None = None
    search_api_key: SecretStr | None = None

    # Privacy: salt used when hashing user identifiers (phone numbers, chat ids)
    user_hash_salt: SecretStr = SecretStr("change-me")

    # Network defaults applied to every outbound call
    http_timeout_seconds: float = Field(default=10.0, gt=0)
    http_max_retries: int = Field(default=2, ge=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
