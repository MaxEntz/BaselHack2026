"""`make deploy` hands Caddy the token the gateway loads, and stops without one (#195, ADR-0019)."""

from pathlib import Path

import pytest
from caddy_env import MISSING, UNSAFE, caddy_env

from core.config import Settings


def settings_from(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line: str) -> Settings:
    monkeypatch.delenv("GATEWAY_TOKEN", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(f"DATABASE_URL=postgresql+psycopg://u:p@localhost/db\n{line}\n")
    return Settings(_env_file=env_file)


@pytest.mark.parametrize(
    "line",
    ["", "GATEWAY_TOKEN=", 'GATEWAY_TOKEN=""', "GATEWAY_TOKEN=''", "GATEWAY_TOKEN=   # fill in"],
)
def test_no_token_stops_the_deploy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line: str
) -> None:
    with pytest.raises(SystemExit, match="Set GATEWAY_TOKEN") as stop:
        caddy_env(settings_from(tmp_path, monkeypatch, line))

    assert stop.value.code == MISSING


def test_caddy_gets_the_value_without_the_inline_comment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_from(tmp_path, monkeypatch, "GATEWAY_TOKEN=abc   # every caller")

    assert caddy_env(settings) == "GATEWAY_TOKEN=abc"


@pytest.mark.parametrize(
    "line", ['GATEWAY_TOKEN="a b"', 'GATEWAY_TOKEN="a\'b"', r"GATEWAY_TOKEN=a\b"]
)
def test_a_token_systemd_would_read_otherwise_stops_the_deploy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line: str
) -> None:
    with pytest.raises(SystemExit) as stop:
        caddy_env(settings_from(tmp_path, monkeypatch, line))

    assert stop.value.code == UNSAFE
