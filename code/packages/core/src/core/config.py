"""Runtime settings read from the environment and from `code/.env` (see `.env.example`)."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_ignore_empty=True, extra="ignore", frozen=True
    )

    database_url: str
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-sonnet-5-5"
    merchant_tz: str = "Europe/Zurich"
    agent_timeout_ms: int = 2000
    gateway_public_url: str | None = None
    gateway_token: SecretStr | None = None
    platform_public_url: str = "http://localhost:8002"
    builder_cache: Literal["read_write", "read_only", "off"] = "read_write"
    crawl_allow_private: bool = False


@lru_cache
def get_settings() -> Settings:
    """Load settings once; fails fast if a required variable such as DATABASE_URL is missing."""
    return Settings()
