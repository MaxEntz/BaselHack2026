"""BATP 0.2 and 0.3: the `TALK` intent, the closed direct intents and the session's version
(PROTOCOL.md §7, §14, #245, #254)."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from batp.client import BatpClient
from batp.codec import MAX_FRAME, encode
from batp.messages import (
    ADDRESSED,
    ALL_INTENTS,
    PROTOCOL_VERSION,
    AgentIdentity,
    NodeIdentity,
    Request,
    Response,
    intents_of,
    negotiate,
)
from batp.server import HANDLER_TIMEOUT, INTENT_TIMEOUTS, NodeServer
from core.errors import AgentUnavailable

CALLER = AgentIdentity(
    id="urn:batp:agent:test", name="Test agent", kind="user-agent", operator="tests"
)
NODE = NodeIdentity(id="urn:batp:node:test", name="Test node")
TALK = {
    "type": "request",
    "id": "r-1",
    "intent": "TALK",
    "merchant": "le-petit-zinc",
    "params": {"message": "Table for 4 tonight?", "idempotency_key": "m-1"},
}


def test_this_implementation_speaks_0_3() -> None:
    assert PROTOCOL_VERSION == "0.3"


def test_talk_is_the_only_addressed_intent() -> None:
    # 0.3 closed the direct booking intents: only the merchant's agent calls the tools (#254).
    assert ALL_INTENTS == ("DISCOVER", "TALK", "RECALL", "PING")
    assert frozenset({"TALK"}) == ADDRESSED


def test_a_talk_frame_round_trips() -> None:
    request = Request.model_validate(TALK)

    assert request.model_dump(mode="json") == TALK


@pytest.mark.parametrize(
    ("caller", "session"),
    [("0.1", "0.1"), ("0.2", "0.2"), ("0.3", "0.3"), ("0.9", "0.3"), ("0.10", "0.3")],
)
def test_the_session_speaks_the_highest_minor_both_speak(caller: str, session: str) -> None:
    assert negotiate(caller) == session


def test_a_0_1_session_is_offered_every_intent_but_talk() -> None:
    assert intents_of("0.1", ALL_INTENTS) == tuple(i for i in ALL_INTENTS if i != "TALK")
    assert intents_of("0.2", ALL_INTENTS) == ALL_INTENTS
    assert intents_of("0.2", ("PING", "TALK")) == ("PING", "TALK")


async def echo(agent: AgentIdentity, request: Request) -> dict[str, Any]:
    return {"intent": request.intent}


async def read_json(reader: asyncio.StreamReader) -> dict[str, Any]:
    frame: dict[str, Any] = json.loads(await asyncio.wait_for(reader.readline(), 2))
    return frame


@pytest.fixture
async def node() -> AsyncIterator[NodeServer]:
    server = NodeServer(node=NODE, handle=echo, authenticate=lambda h: h.token == "s3cret")
    await server.start("127.0.0.1", 0)
    yield server
    await server.stop()


async def hello(
    node: NodeServer, version: str
) -> tuple[dict[str, Any], asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_connection("127.0.0.1", node.port, limit=MAX_FRAME)
    frame = {"type": "hello", "batp": version, "agent": CALLER.model_dump(), "token": "s3cret"}
    writer.write(encode(frame))
    return await read_json(reader), reader, writer


async def test_a_0_1_caller_is_still_welcomed_in_0_1_without_talk(node: NodeServer) -> None:
    welcome, reader, writer = await hello(node, "0.1")
    try:
        assert (welcome["type"], welcome["batp"]) == ("welcome", "0.1")
        assert "TALK" not in welcome["intents"]
        assert welcome["intents"] == ["DISCOVER", "RECALL", "PING"]
        # An intent its version doesn't have isn't served, and the session goes on.
        writer.write(encode(TALK))
        refused = Response.model_validate(await read_json(reader))
        assert (refused.status, refused.error and refused.error.code) == (400, "invalid_input")
        writer.write(encode({"type": "request", "id": "r-2", "intent": "PING", "params": {}}))
        assert (await read_json(reader))["status"] == 200
    finally:
        writer.close()


async def test_a_0_2_caller_is_offered_talk_and_served(node: NodeServer) -> None:
    welcome, reader, writer = await hello(node, "0.2")
    try:
        assert welcome["batp"] == "0.2"
        assert list(welcome["intents"]) == list(ALL_INTENTS)
        writer.write(encode(TALK))
        answer = await read_json(reader)
        assert (answer["status"], answer["result"]) == (200, {"intent": "TALK"})
    finally:
        writer.close()


@pytest.mark.parametrize("version", ["0.1", "0.2", PROTOCOL_VERSION])
@pytest.mark.parametrize("intent", ["AVAILABILITY", "BOOK", "AMEND", "CANCEL"])
async def test_a_closed_intent_is_invalid_in_any_session_and_the_session_goes_on(
    node: NodeServer, version: str, intent: str
) -> None:
    _, reader, writer = await hello(node, version)
    try:
        writer.write(encode(TALK | {"intent": intent}))
        refused = await read_json(reader)
        assert (refused["id"], refused["status"]) == ("r-1", 400)
        assert refused["error"]["code"] == "invalid_input"
        writer.write(encode({"type": "request", "id": "r-2", "intent": "PING", "params": {}}))
        assert (await read_json(reader))["status"] == 200
    finally:
        writer.close()


async def test_talk_needs_a_merchant(node: NodeServer) -> None:
    _, reader, writer = await hello(node, "0.2")
    try:
        writer.write(encode({k: v for k, v in TALK.items() if k != "merchant"}))
        answer = await read_json(reader)
        assert answer["status"] == 400
        assert "TALK needs a merchant" in answer["error"]["message"]
    finally:
        writer.close()


async def test_talk_has_its_own_deadline_and_the_others_keep_theirs() -> None:
    assert (INTENT_TIMEOUTS["TALK"], HANDLER_TIMEOUT) == (25.0, 10.0)

    async def slow(agent: AgentIdentity, request: Request) -> dict[str, Any]:
        await asyncio.sleep(0.2)
        return {"intent": request.intent}

    server = NodeServer(
        node=NODE,
        handle=slow,
        authenticate=lambda h: True,
        handler_timeout=0.05,
        intent_timeouts={"TALK": 1.0},
    )
    await server.start("127.0.0.1", 0)
    try:
        async with BatpClient("127.0.0.1", server.port, CALLER) as client:
            _, ping = await client.exchange("PING")
            _, talk = await client.exchange("TALK", "le-petit-zinc", TALK["params"])
    finally:
        await server.stop()

    assert (ping.status, talk.status) == (503, 200)


async def test_a_talk_lost_with_its_session_is_not_sent_again() -> None:
    calls: list[str] = []
    started = asyncio.Event()

    async def hold(agent: AgentIdentity, request: Request) -> dict[str, Any]:
        calls.append(request.intent)
        started.set()
        await asyncio.sleep(5)
        return {}

    server = NodeServer(node=NODE, handle=hold, authenticate=lambda h: True)
    await server.start("127.0.0.1", 0)
    client = BatpClient("127.0.0.1", server.port, CALLER)
    try:
        async with asyncio.timeout(5):  # a regression must fail, not hang
            talk = asyncio.create_task(client.request("TALK", "le-petit-zinc", TALK["params"]))
            await started.wait()
            for writer in list(server._writers):  # the session drops, the node keeps listening
                writer.close()

            # Not read-only: the caller retries with the same key, the client never does.
            with pytest.raises(AgentUnavailable, match="closed before the answer"):
                await talk
        assert calls == ["TALK"]
    finally:
        await client.close()
        await server.stop()
