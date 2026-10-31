import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.models import MerchantProfile

FIXTURES = Path(__file__).parent / "fixtures"
# The demo's fictional restaurants in Mulhouse (#10), in name order.
DEMO_MERCHANTS = ("la-graine-verte", "le-petit-zinc", "maison-albane")


class _LlmEnv(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    openai_api_key: SecretStr | None = None


def _chromium_installed() -> bool:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        return Path(playwright.chromium.executable_path).exists()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip `llm` tests when no OpenAI key is available (env or .env), `browser` tests when
    Playwright's Chromium is missing."""
    skips: dict[str, pytest.MarkDecorator] = {}
    if not _LlmEnv().openai_api_key:
        skips["llm"] = pytest.mark.skip(reason="OPENAI_API_KEY not set")
    if any("browser" in item.keywords for item in items) and not _chromium_installed():
        skips["browser"] = pytest.mark.skip(
            reason="no Chromium: uv run playwright install chromium"
        )
    for item in items:
        for marker, skip in skips.items():
            if marker in item.keywords:
                item.add_marker(skip)


@pytest.fixture
def profile_data() -> dict[str, Any]:
    path = FIXTURES / "le-petit-zinc.json"
    return json.loads(path.read_text())


@pytest.fixture
def demo_profiles() -> dict[str, MerchantProfile]:
    """The 3 demo merchants by slug, as `scripts/seed.py` provisions them."""
    return {
        slug: MerchantProfile.model_validate_json((FIXTURES / f"{slug}.json").read_text())
        for slug in DEMO_MERCHANTS
    }
