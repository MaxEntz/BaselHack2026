"""Leftover `MATP_*` settings are reported, not silently ignored (#119, ADR-0014)."""

import logging
from pathlib import Path

import pytest

from core import config


def test_legacy_keys_come_from_the_environment_and_the_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("MATP_URL=matp://127.0.0.1:4710\nBATP_TOKEN=abcMATP_xyz\n# MATP_PORT=1\n")

    keys = config.legacy_keys(env_file, environ={"matp_tokens": "{}", "BATP_URL": "x"})

    assert keys == ["MATP_TOKENS", "MATP_URL"]


def test_no_legacy_keys_and_no_env_file(tmp_path: Path) -> None:
    assert config.legacy_keys(tmp_path / ".env", environ={"BATP_URL": "batp://h"}) == []


def test_get_settings_warns_about_legacy_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    monkeypatch.setenv("MATP_URL", "matp://127.0.0.1:4710")
    config.get_settings.cache_clear()
    try:
        with caplog.at_level(logging.WARNING, logger="core.config"):
            settings = config.get_settings()
    finally:
        config.get_settings.cache_clear()

    assert settings.batp_url is None
    assert "MATP_URL ignored since #119" in caplog.text
