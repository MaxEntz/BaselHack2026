"""BATP 0.2: the `TALK` intent and the session's version (PROTOCOL.md §7, §14, #245)."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

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
from batp.server import NodeServer

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


def test_this_implementation_speaks_0_2() -> None:
    assert PROTOCOL_VERSION == "0.2"


def test_talk_is_an_addressed_intent() -> None:
    assert "TALK" in ALL_INTENTS
    assert "TALK" in ADDRESSED


def test_a_talk_frame_round_trips() -> None:
    request = Request.model_validate(TALK)

    assert request.model_dump(mode="json") == TALK


@pytest.mark.parametrize(
    ("caller", "session"), [("0.1", "0.1"), ("0.2", "0.2"), ("0.9", "0.2"), ("0.10", "0.2")]
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
        assert "BOOK" in welcome["intents"]
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


async def test_talk_needs_a_merchant(node: NodeServer) -> None:
    _, reader, writer = await hello(node, "0.2")
    try:
        writer.write(encode({k: v for k, v in TALK.items() if k != "merchant"}))
        answer = await read_json(reader)
        assert answer["status"] == 400
        assert "TALK needs a merchant" in answer["error"]["message"]
    finally:
        writer.close()
