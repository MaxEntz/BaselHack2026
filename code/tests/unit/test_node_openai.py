"""The node's merchant agent on OpenAI (#249): the builder's key and model, a 20 s read
timeout and no SDK retries; no agent at all without a key."""

from typing import Any, cast

import pytest
from openai import AsyncOpenAI
from pydantic import SecretStr

from core.config import Settings
from merchant_node.__main__ import merchant_agent


def settings(**values: Any) -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://unused/db", **values)


def test_the_node_builds_its_agent_on_the_builders_key(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[dict[str, Any]] = []

    def capture(**kwargs: Any) -> AsyncOpenAI:
        built.append(kwargs)
        return AsyncOpenAI(**kwargs)

    monkeypatch.setattr("core.llm.AsyncOpenAI", capture)

    agent = merchant_agent(
        settings(openai_api_key=SecretStr("sk-test")), cast(Any, None), cast(Any, None)
    )

    assert agent is not None
    [kwargs] = built
    assert kwargs["api_key"] == "sk-test"
    assert (kwargs["timeout"].read, kwargs["timeout"].connect) == (20, 5)
    assert kwargs["max_retries"] == 0


def test_without_a_key_the_node_has_no_agent() -> None:
    assert merchant_agent(settings(), cast(Any, None), cast(Any, None)) is None
