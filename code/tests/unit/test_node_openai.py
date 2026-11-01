"""The node's wiring (#249): the merchant agent on the builder's OpenAI key and model, a 20 s
read timeout and no SDK retries, no agent at all without a key; a stop that waits for `TALK`."""

from typing import Any, cast

import pytest
from openai import AsyncOpenAI
from pydantic import SecretStr

from batp.server import TALK_TIMEOUT
from core.config import Settings
from merchant_node.__main__ import merchant_agent, node_server
from merchant_node.agents import MerchantAgents
from merchant_node.requests_log import RequestLog


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


def test_the_agent_uses_the_builders_model_and_its_own_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[tuple[Any, ...]] = []

    class Capture:
        def __init__(self, connection: Any, model: str, **kwargs: Any) -> None:
            built.append((model, kwargs))

    monkeypatch.setattr("merchant_node.__main__.OpenAIClient", Capture)
    merchant_agent(
        settings(
            openai_api_key=SecretStr("sk-test"),
            openai_model="gpt-test",
            agent_reasoning_effort="medium",
        ),
        cast(Any, None),
        cast(Any, None),
    )
    assert built == [("gpt-test", {"reasoning_effort": "medium"})]


def test_a_stop_waits_for_the_answers_in_flight() -> None:
    agents = MerchantAgents(cast(Any, None), cast(Any, None))
    record = RequestLog(cast(Any, None))
    server = node_server(settings(), agents, record)

    assert server.handle is agents and server.record is record
    assert server.intent_timeouts == {"TALK": TALK_TIMEOUT} and TALK_TIMEOUT == 25
    # A `TALK` in flight may report a write already made: a stop doesn't cut it.
    assert server.stop_grace == TALK_TIMEOUT
    # Nor the gateway's whole tool call, when it waits longer.
    assert node_server(settings(agent_timeout_ms=40_000), agents, record).stop_grace == 40


def test_without_a_key_the_node_has_no_agent() -> None:
    assert merchant_agent(settings(), cast(Any, None), cast(Any, None)) is None
