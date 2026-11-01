"""Workspace smoke tests: every package imports, nothing shadows the stdlib."""

import importlib
import json
import platform
from importlib.resources import files

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from core.config import Settings, get_settings
from merchant_platform import api

MODULES = [
    "core.models",
    "core.db",
    "core.availability",
    "core.errors",
    "core.config",
    "agent.service",
    "agent.provisioning",
    "gateway.server",
    "agent.discovery",
    "gateway.fallback",
    "gateway.__main__",
    "builder.crawl",
    "builder.extract",
    "builder.build",
    "merchant_platform.api",
    "merchant_platform.assistant",
    "merchant_platform.events",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name: str) -> None:
    importlib.import_module(name)


def test_stdlib_platform_is_not_shadowed() -> None:
    assert callable(platform.python_version)


def test_restaurant_template_ships_with_agent() -> None:
    template = json.loads((files("agent") / "templates" / "restaurant.json").read_text())
    assert template["vertical"] == "restaurant"
    assert template["defaults"]["slot_length_min"] == 120


def test_settings_require_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_settings_treat_empty_values_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("GATEWAY_TOKEN", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.gateway_token is None
    assert settings.openai_api_key is None


def test_platform_health() -> None:
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@localhost:5432/db")
    response = TestClient(api.create_app(settings)).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_platform_app_is_built_on_first_access(monkeypatch: pytest.MonkeyPatch) -> None:
    # `uvicorn merchant_platform.api:app` reads the settings then, not at import time.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setattr(api, "configure_logging", lambda: None)
    get_settings.cache_clear()
    api._default_app.cache_clear()
    try:
        assert api.app is api.app
        assert TestClient(api.app).get("/health").status_code == 200
    finally:
        get_settings.cache_clear()
        api._default_app.cache_clear()
