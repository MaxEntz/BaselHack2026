"""`make deploy` stops when the node would load no OpenAI key (#250)."""

from pathlib import Path

import pytest
from node_env import MISSING, check

from core.config import Settings


def settings_from(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line: str) -> Settings:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(f"DATABASE_URL=postgresql+psycopg://u:p@localhost/db\n{line}\n")
    return Settings(_env_file=env_file)


@pytest.mark.parametrize(
    "line",
    [
        "",
        "OPENAI_API_KEY=",
        'OPENAI_API_KEY=""',
        "OPENAI_API_KEY=''",
        "OPENAI_API_KEY=   # fill in",
    ],
)
def test_no_key_stops_the_deploy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line: str
) -> None:
    with pytest.raises(SystemExit) as stop:
        check(settings_from(tmp_path, monkeypatch, line))

    assert stop.value.code == MISSING


def test_a_node_with_a_key_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    check(settings_from(tmp_path, monkeypatch, "OPENAI_API_KEY=sk-test   # the builder's"))
