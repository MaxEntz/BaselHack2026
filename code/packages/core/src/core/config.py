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
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-6.1-terra"
    merchant_tz: str = "Europe/Zurich"
    agent_timeout_ms: int = 2000
    # Fault injection for demos and tests (#13): delay before each agent call, only for these
    # comma-separated slugs (empty = every merchant).
    chaos_delay_ms: int = 0
    chaos_merchants: str = ""
    gateway_public_url: str | None = None
    gateway_token: SecretStr | None = None
    platform_public_url: str = "http://localhost:8002"
    # Origins allowed to call the platform's public booking API (the showcase site's form).
    public_cors_origins: tuple[str, ...] = ("http://localhost:8080", "http://127.0.0.1:8080")
    # Dev-only routes such as POST /dev/reset-session.
    platform_dev_routes: bool = True
    # Merchant the chat starts with, skipping onboarding (the web chat's VITE_MERCHANT_SLUG).
    platform_merchant: str | None = None
    # Reasoning effort of the platform assistant: lower is faster to the first word.
    openai_reasoning_effort: str | None = "low"
    builder_cache: Literal["read_write", "read_only", "off"] = "read_write"
    crawl_allow_private: bool = False


@lru_cache
def get_settings() -> Settings:
    """Load settings once; fails fast if a required variable such as DATABASE_URL is missing."""
    return Settings()
