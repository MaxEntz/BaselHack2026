import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class _LlmEnv(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    anthropic_api_key: SecretStr | None = None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip `llm` tests when no Anthropic key is available (env or .env)."""
    if _LlmEnv().anthropic_api_key:
        return
    skip_llm = pytest.mark.skip(reason="ANTHROPIC_API_KEY not set")
    for item in items:
        if "llm" in item.keywords:
            item.add_marker(skip_llm)


@pytest.fixture
def profile_data() -> dict[str, Any]:
    path = Path(__file__).parent / "fixtures" / "le-petit-zinc.json"
    return json.loads(path.read_text())
