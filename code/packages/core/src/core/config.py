"""Runtime settings read from the environment and from `code/.env` (see `.env.example`)."""

import logging
import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
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
    # Where the gateway reaches the merchant agents: `batp://host[:port]`, with this agent's token.
    # Unset: they run in the gateway's process.
    batp_url: str | None = None
    batp_token: SecretStr | None = None
    # Fault injection for demos and tests (#13): delay before each agent call, only for these
    # comma-separated slugs (empty = every merchant).
    chaos_delay_ms: int = 0
    chaos_merchants: str = ""
    gateway_public_url: str | None = None
    gateway_token: SecretStr | None = None
    platform_public_url: str = "http://localhost:8002"
    # Origins allowed to call the platform's public booking API (the showcase site's form).
    public_cors_origins: tuple[str, ...] = ("http://localhost:8080", "http://127.0.0.1:8080")
    # Dev-only routes such as POST /dev/reset-session: off unless asked (`.env.example` sets it).
    platform_dev_routes: bool = False
    # Development only: the merchant the chat starts with, skipping onboarding (the web chat's
    # VITE_MERCHANT_SLUG), whose pages and live events then open without its link (#132).
    platform_merchant: str | None = None
    # Where merchants open the web app: the start of the secret links shown after a build.
    platform_web_url: str = "http://localhost:5173"
    # Signs the owner and invite cookies (#132). Unset: a random key per process, so cookies
    # don't survive a restart.
    platform_cookie_secret: SecretStr | None = None
    # Needed to start a new build, so that strangers can't spend the OpenAI key. Unset (local
    # development): nothing is asked.
    platform_invite_code: SecretStr | None = None
    # Reasoning effort of the platform assistant: lower is faster to the first word.
    openai_reasoning_effort: str | None = "low"
    # Reasoning effort of the builder's extraction (spike #4: `none` is as accurate, faster).
    builder_reasoning_effort: str | None = "none"
    builder_cache: Literal["read_write", "read_only", "off"] = "read_write"
    crawl_allow_private: bool = False
    # BATP node (documentation/PROTOCOL.md): loopback only in the POC (ADR-0011).
    batp_host: str = "127.0.0.1"
    batp_port: int = 4710
    # Known agents and their tokens, as JSON: {"urn:batp:agent:claude-gateway": "…"}.
    batp_tokens: dict[str, SecretStr] = {}
    # TLS for the node, required when it listens beyond loopback (PEM files).
    batp_tls_cert: str | None = None
    batp_tls_key: str | None = None


# Settings named after MATP, which #119 renamed BATP (ADR-0014).
LEGACY_PREFIX = "MATP_"

log = logging.getLogger("core.config")


def legacy_keys(
    env_file: str | Path = ".env", environ: Mapping[str, str] | None = None
) -> list[str]:
    """`MATP_*` keys still set in the environment or in `env_file`, sorted.

    `Settings` ignores unknown keys, so a leftover `MATP_URL` would silently make the gateway
    run its agent in-process instead of bridging to the node.
    """
    environ = os.environ if environ is None else environ
    keys = {key.upper() for key in environ}
    if Path(env_file).is_file():
        keys |= {key.upper() for key in dotenv_values(env_file)}
    return sorted(key for key in keys if key.startswith(LEGACY_PREFIX))


@lru_cache
def get_settings() -> Settings:
    """Load settings once; fails fast if a required variable such as DATABASE_URL is missing.

    Warns about leftover `MATP_*` keys, which are ignored.
    """
    if keys := legacy_keys():
        log.warning(
            "%s ignored since #119: rename them BATP_* (urn:batp:, batp://)", ", ".join(keys)
        )
    return Settings()
