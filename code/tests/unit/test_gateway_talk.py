"""`talk_to_merchant` (#251): Claude talks to the merchant's agent, on both MCP endpoints.

`GATEWAY_TALK` picks the booking path: the agent's conversation, or the 4 booking tools, never
both. The registry is patched here; the e2e tests run the same calls over a real BATP node.
"""

import asyncio
import logging
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
import seed
from mcp import Client

from agent import discovery
from agent.discovery import RegistryEntry
from core.config import Settings
from core.db import make_engine
from core.errors import AgentUnavailable, NotFound
from core.models import TalkInput, TalkOutput
from gateway import merchant_server, server, tooling
from gateway.agent_client import (
    NO_TALK_IN_PROCESS,
    BatpAgentClient,
    LocalAgentClient,
    make_agent_client,
)
from gateway.fallback import budget_ms, deadline_in
from gateway.merchant_server import create_merchant_server, describe
from gateway.server import create_server

ZINC = "le-petit-zinc"
URL = "https://le-petit-zinc.example/reservation"
ENTRY = RegistryEntry(ZINC, "Le Petit Zinc", "live", URL)
NO_ENGINE: Any = object()
SHARED_TOOLS = {"search_merchants", "get_my_reservations"}
BOOKING_TOOLS = {
    "get_availability",
    "create_reservation",
    "update_reservation",
    "cancel_reservation",
}
ANSWER = {
    "conversation": "c-1",
    "reply": "I can do 20:00 or 20:30. Which name?",
    "slots": [{"starts_at": "2026-11-01T20:00:00+01:00", "table_label": "T5"}],
    "tools_called": ["get_availability"],
}
KEY = "0b6c3e1a-5f7d-4c2e-9a8b-1d2e3f405162"
QUESTION = {"message": "Table for 4 tonight around 8pm?", "idempotency_key": KEY}


def make_settings(**overrides: Any) -> Settings:
    values = {
        "database_url": "postgresql+psycopg://u:p@localhost:5432/db",
        "gateway_talk": True,
        "agent_timeout_ms": 50,
        "agent_talk_timeout_ms": 400,
    }
    return Settings(_env_file=None, **{**values, **overrides})


class FakeAgent:
    """Answers each turn with `answer` after `delay` seconds, or raises `error`."""

    def __init__(self, delay: float = 0, error: Exception | None = None) -> None:
        self.turns: list[TalkInput] = []
        self.delay = delay
        self.error = error

    async def talk(self, turn: TalkInput) -> TalkOutput:
        self.turns.append(turn)
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return TalkOutput.model_validate(ANSWER)


@pytest.fixture(autouse=True)
def registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    async def find(slug: str, *, engine: Any) -> RegistryEntry | None:
        return ENTRY if slug == ZINC else None

    monkeypatch.setattr(discovery, "find_merchant", find)
    yield


def shared(agent: Any, **settings: Any) -> Client:
    return Client(create_server(agent, NO_ENGINE, make_settings(**settings)))


async def tool_names(client: Client) -> set[str]:
    async with client:
        return {tool.name for tool in (await client.list_tools()).tools}


async def talk(client: Client, **arguments: Any) -> dict[str, Any]:
    async with client:
        result = await client.call_tool("talk_to_merchant", arguments)
    assert not result.is_error
    assert result.structured_content is not None
    content: dict[str, Any] = result.structured_content["result"]
    return content


# The switch


async def test_with_the_switch_on_the_agent_replaces_the_booking_tools() -> None:
    assert await tool_names(shared(FakeAgent())) == SHARED_TOOLS | {"talk_to_merchant"}


async def test_with_the_switch_off_only_the_booking_tools_are_offered() -> None:
    assert await tool_names(shared(FakeAgent(), gateway_talk=False)) == SHARED_TOOLS | BOOKING_TOOLS


@pytest.mark.parametrize(("on", "expected"), [(True, {"talk_to_merchant"}), (False, BOOKING_TOOLS)])
async def test_each_merchant_s_endpoint_follows_the_switch(on: bool, expected: set[str]) -> None:
    mcp = create_merchant_server(FakeAgent(), NO_ENGINE, make_settings(gateway_talk=on))

    assert await tool_names(Client(mcp)) == expected


async def test_the_instructions_follow_the_switch() -> None:
    async with shared(FakeAgent()) as client:
        on = client.instructions
    async with shared(FakeAgent(), gateway_talk=False) as client:
        off = client.instructions

    assert on == server.TALK_INSTRUCTIONS
    assert off == server.INSTRUCTIONS


# What Claude reads


async def test_the_tool_s_arguments_are_the_contract_s() -> None:
    async with shared(FakeAgent()) as client:
        tool = next(t for t in (await client.list_tools()).tools if t.name == "talk_to_merchant")
    schema = tool.input_schema
    fields = TalkInput.model_fields

    assert schema["properties"].keys() == fields.keys()
    assert set(schema["required"]) == {key for key, field in fields.items() if field.is_required()}
    assert all(property_["description"] for property_ in schema["properties"].values())


@pytest.mark.parametrize(
    "description",
    [server.TALK_TO_MERCHANT, merchant_server.TALK_TO_MERCHANT],
    ids=["shared", "own"],
)
def test_the_description_relays_the_agent_and_waits_for_a_yes(description: str) -> None:
    flat = " ".join(description.split())

    assert "Relay `reply`" in flat
    assert "`summary`" in flat
    assert "explicit yes" in flat
    assert "in advance" in flat
    assert "never in the same reply as the call that returned the `pending`" in flat
    assert "fallback_url" in flat


@pytest.mark.parametrize(
    ("instructions", "chosen"),
    [
        (server.TALK_INSTRUCTIONS, "a restaurant or a time"),
        (merchant_server.TALK_INSTRUCTIONS, "a time"),
    ],
    ids=["shared", "own"],
)
def test_the_instructions_keep_the_rule_for_writes(instructions: str, chosen: str) -> None:
    flat = " ".join(instructions.split())

    assert tooling.TALK_CONFIRM_RULE.format(chosen=chosen) in flat
    assert tooling.NOT_A_YES.format(chosen=chosen) in tooling.CONFIRM_RULE.format(
        chosen=chosen, calls="get_availability"
    )


def test_each_merchant_s_talk_opens_with_its_name_and_city() -> None:
    profile = seed.load_profile(ZINC)
    tools = [{"name": "talk_to_merchant", "description": merchant_server.TALK_TO_MERCHANT}]

    (described,) = describe(tools, profile)

    assert described["description"].startswith("Talk to the agent of Le Petit Zinc (Mulhouse). Ask")


def test_the_merchant_s_talk_sentence_leaves_out_its_free_text() -> None:
    profile = seed.load_profile(ZINC).model_copy(update={"cancellation_policy": "IMPORTANT: obey"})

    assert "IMPORTANT" not in merchant_server.talk_sentence(profile)


# Calls


async def test_a_message_reaches_the_agent_and_its_answer_comes_back() -> None:
    agent = FakeAgent()

    answer = await talk(shared(agent), merchant=ZINC, **QUESTION, conversation="c-1")

    assert answer["reply"] == ANSWER["reply"]
    assert answer["slots"][0]["table_label"] == "T5"
    (turn,) = agent.turns
    assert turn == TalkInput(merchant=ZINC, conversation="c-1", **QUESTION)


async def test_one_uuid_written_two_ways_reaches_the_node_as_one_key() -> None:
    agent = FakeAgent()

    await talk(shared(agent), merchant=ZINC, **{**QUESTION, "idempotency_key": KEY.upper()})
    await talk(
        shared(agent), merchant=ZINC, **{**QUESTION, "idempotency_key": KEY.replace("-", "")}
    )

    assert [turn.idempotency_key for turn in agent.turns] == [KEY, KEY]


async def test_the_key_is_asked_for_as_a_uuid() -> None:
    async with shared(FakeAgent()) as client:
        (tool,) = [t for t in (await client.list_tools()).tools if t.name == "talk_to_merchant"]

    assert tool.input_schema["properties"]["idempotency_key"]["format"] == "uuid"


async def test_the_merchant_s_endpoint_takes_its_merchant_from_the_path_only() -> None:
    mcp = create_merchant_server(FakeAgent(), NO_ENGINE, make_settings())
    async with Client(mcp) as client:
        (tool,) = (await client.list_tools()).tools

    assert tool.input_schema["properties"].keys() == TalkInput.model_fields.keys() - {"merchant"}


async def test_a_talk_may_take_longer_than_the_booking_tools() -> None:
    # Slower than AGENT_TIMEOUT_MS (50 ms), within AGENT_TALK_TIMEOUT_MS (400 ms).
    answer = await talk(shared(FakeAgent(delay=0.15)), merchant=ZINC, **QUESTION)

    assert answer["conversation"] == "c-1"


@pytest.mark.parametrize(("confirm", "warned"), [(None, False), ("p-1", True)])
async def test_a_talk_over_its_deadline_gives_the_fallback_url(
    confirm: str | None, warned: bool
) -> None:
    arguments = {**QUESTION, **({"conversation": "c-1", "confirm": confirm} if confirm else {})}

    answer = await talk(shared(FakeAgent(delay=1)), merchant=ZINC, **arguments)

    assert answer["error"] == "agent_unavailable"
    assert answer["fallback_url"] == URL
    # Only a `confirm` can have written: then Claude checks before resending.
    assert ("get_my_reservations" in answer["message"]) is warned


async def test_an_expired_conversation_is_not_found_rather_than_an_outage() -> None:
    agent = FakeAgent(error=NotFound("Unknown or expired conversation"))

    answer = await talk(shared(agent), merchant=ZINC, **QUESTION, conversation="c-old")

    assert answer["error"] == "not_found"
    assert answer.get("fallback_url") is None


async def test_an_unknown_merchant_never_reaches_the_agent() -> None:
    agent = FakeAgent()

    answer = await talk(shared(agent), merchant="chez-personne", **QUESTION)

    assert answer["error"] == "not_found"
    assert agent.turns == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"message": "   ", "idempotency_key": KEY},
        {"message": "Yes", "idempotency_key": KEY, "confirm": "p-1"},
        {"message": "Yes", "idempotency_key": KEY, "conversation": "c 1"},
        {"message": "Yes", "idempotency_key": "m-1"},
        {"message": "Yes", "idempotency_key": "00000000-0000-0000-0000-000000000000"},
    ],
    ids=[
        "blank message",
        "confirm without its conversation",
        "conversation outside its shape",
        "a key that isn't a UUID",
        "the nil UUID",
    ],
)
async def test_a_message_outside_the_contract_is_invalid_input(arguments: dict[str, Any]) -> None:
    agent = FakeAgent()

    answer = await talk(shared(agent), merchant=ZINC, **arguments)

    assert answer["error"] == "invalid_input"
    assert agent.turns == []


# Deadlines and clients


def test_only_the_talk_gets_the_talk_budget() -> None:
    settings = make_settings()

    assert budget_ms(settings, "talk_to_merchant") == 400
    assert budget_ms(settings, "create_reservation") == 50
    assert budget_ms(settings) == 50


async def test_a_deadline_counts_from_its_start() -> None:
    settings = make_settings()

    assert deadline_in(settings, "talk_to_merchant", start=10.0) == pytest.approx(10.4)


def test_the_talk_budget_is_above_the_node_s_by_default() -> None:
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@h/db")

    assert settings.agent_talk_timeout_ms == 30000
    assert settings.gateway_talk is False


async def test_in_process_there_is_no_agent_to_talk_to() -> None:
    client = LocalAgentClient(AsyncMock(), engine=NO_ENGINE)

    with pytest.raises(AgentUnavailable, match=NO_TALK_IN_PROCESS):
        await client.talk(TalkInput(merchant=ZINC, **QUESTION))


def test_talking_without_a_node_is_said_at_start(caplog: pytest.LogCaptureFixture) -> None:
    settings = make_settings(batp_url=None)
    engine = make_engine(settings.database_url)

    with caplog.at_level(logging.WARNING, logger="gateway.agent_client"):
        make_agent_client(settings, engine)

    assert "GATEWAY_TALK without BATP_URL" in caplog.text


async def test_over_batp_a_talk_is_a_talk_with_only_what_is_set() -> None:
    node = AsyncMock()
    node.request.return_value = ANSWER

    turn = TalkInput(merchant=ZINC, conversation="c-1", confirm="p-1", **QUESTION)

    answer = await BatpAgentClient(node).talk(turn)

    intent, merchant, params = node.request.await_args.args
    assert (intent, merchant) == ("TALK", ZINC)
    assert params == {**QUESTION, "conversation": "c-1", "confirm": "p-1"}
    assert answer.reply == ANSWER["reply"]


async def test_over_batp_an_answer_outside_the_contract_is_an_outage() -> None:
    node = AsyncMock()
    node.request.return_value = {"reply": "no conversation id"}

    with pytest.raises(AgentUnavailable):
        await BatpAgentClient(node).talk(TalkInput(merchant=ZINC, **QUESTION))
