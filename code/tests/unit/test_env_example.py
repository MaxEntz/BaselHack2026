"""`.env.example` names every setting, set or commented out, and only settings (#220)."""

import re
from pathlib import Path

from core.config import LEGACY_PREFIX, Settings

ENV_EXAMPLE = Path(__file__).parents[2] / ".env.example"
# `KEY=…`, or commented out as `# KEY=…`.
KEY = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=", re.MULTILINE)
# Keys of `.env.example` that no `Settings` field reads, and who reads them.
NOT_SETTINGS = {"DB_PORT": "docker-compose.yml, for the host port of Postgres"}


def example_keys() -> set[str]:
    return set(KEY.findall(ENV_EXAMPLE.read_text()))


def setting_keys() -> set[str]:
    """Each field's environment variable: the prefix, then its name in capitals."""
    prefix = Settings.model_config.get("env_prefix", "")
    return {f"{prefix}{name}".upper() for name in Settings.model_fields}


def test_every_setting_is_in_the_env_example() -> None:
    assert sorted(setting_keys() - example_keys()) == []


def test_every_key_of_the_env_example_is_read() -> None:
    # `Settings` ignores unknown keys: a misspelt or renamed one would do nothing, silently.
    unread = example_keys() - setting_keys() - NOT_SETTINGS.keys()

    assert sorted(unread) == []
    assert not any(key.startswith(LEGACY_PREFIX) for key in example_keys())
