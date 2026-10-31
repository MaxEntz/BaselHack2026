"""Workspace smoke tests: every package imports, nothing shadows the stdlib."""

import importlib
import json
import platform
from importlib.resources import files

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from core.config import Settings
from merchant_platform.api import app

MODULES = [
    "core.models",
    "core.db",
    "core.availability",
    "core.errors",
    "core.config",
    "agent.service",
    "agent.provisioning",
    "gateway.server",
    "gateway.discovery",
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
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.gateway_token is None
    assert settings.anthropic_api_key is None


def test_platform_health() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
