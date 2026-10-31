"""`make deploy` checks the settings the platform loads, not the raw lines of its `.env` (#142)."""

from pathlib import Path

import pytest
from platform_env import check

from core.config import Settings

DEPLOYED = {
    "PLATFORM_WEB_URL": "https://sesame.191-96-88-152.sslip.io",
    "PLATFORM_COOKIE_SECRET": "c" * 32,
    "PLATFORM_INVITE_CODE": "invite-code",
}


def settings_from(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **lines: str) -> Settings:
    keys = {*DEPLOYED, "PLATFORM_DEV_ROUTES", "PLATFORM_MERCHANT"}
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    body = "\n".join(f"{key}={value}" for key, value in {**DEPLOYED, **lines}.items())
    env_file.write_text(f"DATABASE_URL=postgresql+psycopg://u:p@localhost/db\n{body}\n")
    return Settings(_env_file=env_file)


def test_a_deployed_platform_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    check(settings_from(tmp_path, monkeypatch, PLATFORM_DEV_ROUTES="0"))


@pytest.mark.parametrize("value", ['""', "''", "   # fill in", ""])
@pytest.mark.parametrize(
    ("key", "reason"),
    [
        ("PLATFORM_INVITE_CODE", "Set PLATFORM_INVITE_CODE"),
        ("PLATFORM_COOKIE_SECRET", "Set PLATFORM_COOKIE_SECRET"),
    ],
)
def test_a_value_the_platform_reads_as_unset_stops_the_deploy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str, reason: str, value: str
) -> None:
    with pytest.raises(SystemExit, match=reason):
        check(settings_from(tmp_path, monkeypatch, **{key: value}))


@pytest.mark.parametrize(
    ("lines", "reason"),
    [
        ({"PLATFORM_DEV_ROUTES": "1"}, "PLATFORM_DEV_ROUTES must be off"),
        ({"PLATFORM_WEB_URL": "http://sesame.191-96-88-152.sslip.io"}, "the https sesame. host"),
        ({"PLATFORM_INVITE_CODE": "short"}, "PLATFORM_INVITE_CODE needs at least"),
        ({"PLATFORM_COOKIE_SECRET": "too-short"}, "PLATFORM_COOKIE_SECRET needs at least"),
        ({"PLATFORM_MERCHANT": "la-graine-verte"}, "PLATFORM_MERCHANT is for local development"),
    ],
)
def test_an_open_or_weak_platform_stops_the_deploy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lines: dict[str, str], reason: str
) -> None:
    with pytest.raises(SystemExit, match=reason):
        check(settings_from(tmp_path, monkeypatch, **lines))
