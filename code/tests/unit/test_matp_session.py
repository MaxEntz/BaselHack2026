"""MATP sessions over real TCP on loopback, with a fake backend (PROTOCOL.md §3, §5, §6, §10)."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest

from core.errors import AgentUnavailable, InvalidInput, SlotUnavailable
from matp.client import MatpClient, MatpRejected
from matp.codec import MAX_FRAME, encode
from matp.messages import AgentIdentity, Hello, NodeIdentity, Request
from matp.server import NodeServer

CALLER = AgentIdentity(
    id="urn:matp:agent:test", name="Test agent", kind="user-agent", operator="tests"
)
NODE = NodeIdentity(id="urn:matp:node:test", name="Test node")


async def fake_backend(agent: AgentIdentity, request: Request) -> dict[str, Any]:
    if request.intent == "PING":
        await asyncio.sleep(float(request.params.get("delay", 0)))
        return {"pong": True, "agent": agent.id}
    if request.intent == "BOOK":
        if request.params.get("delay"):
            await asyncio.sleep(float(request.params["delay"]))
            return {"booked": True}
        raise SlotUnavailable("No table left at 20:00")
    if request.intent == "RECALL":
        return {"blob": "a" * (MAX_FRAME + 10)}
    if request.intent == "AMEND":
        raise RuntimeError("database exploded at line 42")
    raise InvalidInput(f"{request.intent} is not served here")


def accept_token(hello: Hello) -> bool:
    return hello.token == "s3cret"


@pytest.fixture
async def node() -> AsyncIterator[NodeServer]:
    server = NodeServer(node=NODE, handle=fake_backend, authenticate=accept_token)
    await server.start("127.0.0.1", 0)
    yield server
    await server.stop()


def client_for(node: NodeServer, token: str | None = "s3cret") -> MatpClient:
    return MatpClient("127.0.0.1", node.port, CALLER, token=token)


async def raw_session(node: NodeServer) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection("127.0.0.1", node.port, limit=MAX_FRAME)


async def read_json(reader: asyncio.StreamReader) -> dict[str, Any]:
    line = await asyncio.wait_for(reader.readline(), 2)
    frame: dict[str, Any] = json.loads(line)
    return frame


async def test_hello_then_requests(node: NodeServer) -> None:
    async with client_for(node) as client:
        result = await client.request("PING")

        assert result == {"pong": True, "agent": "urn:matp:agent:test"}
        assert client.welcome is not None
        assert "PING" in client.welcome.intents
        assert client.welcome.node == NODE


async def test_the_caller_can_name_its_request(
    node: NodeServer, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="matp"):
        async with client_for(node) as client:
            assert await client.request("PING", request_id="r-mine") == {
                "pong": True,
                "agent": CALLER.id,
            }

    lines = [r.__dict__ for r in caplog.records if r.getMessage() == "matp request"]
    assert [line["request"] for line in lines] == ["r-mine"]


async def test_a_wrong_token_is_rejected_with_401(node: NodeServer) -> None:
    with pytest.raises(MatpRejected) as rejected:
        async with client_for(node, token="wrong") as client:
            await client.request("PING")

    assert rejected.value.status == 401


async def test_another_major_version_is_rejected_with_426(node: NodeServer) -> None:
    reader, writer = await raw_session(node)
    hello = {"type": "hello", "matp": "1.0", "agent": CALLER.model_dump(), "token": "s3cret"}
    writer.write(encode(hello))

    assert (await read_json(reader))["status"] == 426
    assert await reader.read() == b""
    writer.close()


async def test_the_first_frame_must_be_hello(node: NodeServer) -> None:
    reader, writer = await raw_session(node)
    writer.write(encode({"type": "request", "id": "r-1", "intent": "PING", "params": {}}))

    reply = await read_json(reader)
    assert (reply["type"], reply["status"]) == ("reject", 400)
    assert await reader.read() == b""
    writer.close()


async def test_responses_come_back_by_id_in_any_order(node: NodeServer) -> None:
    async with client_for(node) as client:
        slow = asyncio.create_task(client.request("PING", params={"delay": 0.3}))
        fast = asyncio.create_task(client.request("PING", params={"delay": 0}))

        done, _ = await asyncio.wait({slow, fast}, return_when=asyncio.FIRST_COMPLETED)

        assert done == {fast}
        assert (await slow)["pong"] is True


async def test_business_errors_reach_the_caller_as_agent_errors(node: NodeServer) -> None:
    async with client_for(node) as client:
        with pytest.raises(SlotUnavailable, match="No table left at 20:00"):
            await client.request("BOOK", merchant="le-petit-zinc", params={})


async def test_an_unexpected_failure_is_503_without_details(node: NodeServer) -> None:
    async with client_for(node) as client:
        with pytest.raises(AgentUnavailable) as raised:
            await client.request("AMEND", merchant="le-petit-zinc", params={})

    assert "line 42" not in str(raised.value)
    assert "exploded" not in str(raised.value)


async def test_malformed_requests_get_400_and_the_session_goes_on(node: NodeServer) -> None:
    reader, writer = await raw_session(node)
    hello = {"type": "hello", "matp": "0.1", "agent": CALLER.model_dump(), "token": "s3cret"}
    writer.write(encode(hello))
    assert (await read_json(reader))["type"] == "welcome"

    writer.write(encode({"type": "request", "id": "r-1", "intent": "STEAL", "params": {}}))
    writer.write(b"this is not json\n")
    writer.write(encode({"type": "request", "id": "r-2", "intent": "PING", "params": {}}))

    replies = [await read_json(reader) for _ in range(3)]
    assert [(r.get("id"), r["status"]) for r in replies] == [
        ("r-1", 400),
        (None, 400),
        ("r-2", 200),
    ]
    writer.close()


async def test_an_oversized_frame_gets_413_and_closes_the_session(node: NodeServer) -> None:
    reader, writer = await raw_session(node)
    hello = {"type": "hello", "matp": "0.1", "agent": CALLER.model_dump(), "token": "s3cret"}
    writer.write(encode(hello))
    await read_json(reader)

    writer.write(b'{"type": "request", "id": "big", "params": "' + b"a" * MAX_FRAME + b'"}\n')
    await writer.drain()

    assert (await read_json(reader))["status"] == 413
    assert await asyncio.wait_for(reader.read(), 2) == b""
    writer.close()


async def test_requests_beyond_the_in_flight_limit_get_400() -> None:
    server = NodeServer(node=NODE, handle=fake_backend, authenticate=accept_token, max_in_flight=1)
    await server.start("127.0.0.1", 0)
    try:
        async with MatpClient("127.0.0.1", server.port, CALLER, token="s3cret") as client:
            slow = asyncio.create_task(client.request("PING", params={"delay": 0.3}))
            await asyncio.sleep(0.05)
            with pytest.raises(InvalidInput, match="in flight"):
                await client.request("PING")
            assert (await slow)["pong"] is True
    finally:
        await server.stop()


async def test_an_idle_session_is_closed() -> None:
    server = NodeServer(node=NODE, handle=fake_backend, authenticate=accept_token, idle_timeout=0.2)
    await server.start("127.0.0.1", 0)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port, limit=MAX_FRAME)
        hello = {"type": "hello", "matp": "0.1", "agent": CALLER.model_dump(), "token": "s3cret"}
        writer.write(encode(hello))
        await read_json(reader)

        assert await asyncio.wait_for(reader.read(), 2) == b""
        writer.close()
    finally:
        await server.stop()


async def test_an_unreachable_node_is_agent_unavailable() -> None:
    server = NodeServer(node=NODE, handle=fake_backend, authenticate=accept_token)
    await server.start("127.0.0.1", 0)
    port = server.port
    await server.stop()

    with pytest.raises(AgentUnavailable):
        async with MatpClient("127.0.0.1", port, CALLER, token="s3cret") as client:
            await client.request("PING")


async def test_the_client_reconnects_once_after_the_node_restarts() -> None:
    server = NodeServer(node=NODE, handle=fake_backend, authenticate=accept_token)
    await server.start("127.0.0.1", 0)
    port = server.port
    client = MatpClient("127.0.0.1", port, CALLER, token="s3cret")
    try:
        assert (await client.request("PING"))["pong"] is True
        await server.stop()
        server = NodeServer(node=NODE, handle=fake_backend, authenticate=accept_token)
        await server.start("127.0.0.1", port)

        assert (await client.request("PING"))["pong"] is True
    finally:
        await client.close()
        await server.stop()


async def test_a_handler_past_its_timeout_is_503() -> None:
    server = NodeServer(
        node=NODE, handle=fake_backend, authenticate=accept_token, handler_timeout=0.1
    )
    await server.start("127.0.0.1", 0)
    try:
        async with MatpClient("127.0.0.1", server.port, CALLER, token="s3cret") as client:
            with pytest.raises(AgentUnavailable, match="took too long"):
                await client.request("PING", params={"delay": 0.5})
            assert (await client.request("PING"))["pong"] is True
    finally:
        await server.stop()


async def test_an_answer_too_large_to_send_is_503(node: NodeServer) -> None:
    async with client_for(node) as client:
        with pytest.raises(AgentUnavailable, match="too large"):
            await client.request("RECALL", params={})
        assert (await client.request("PING"))["pong"] is True


async def test_sessions_beyond_the_limit_are_refused() -> None:
    server = NodeServer(node=NODE, handle=fake_backend, authenticate=accept_token, max_sessions=1)
    await server.start("127.0.0.1", 0)
    try:
        async with MatpClient("127.0.0.1", server.port, CALLER, token="s3cret") as first:
            await first.request("PING")
            reader, writer = await asyncio.open_connection("127.0.0.1", server.port)

            assert (await read_json(reader))["status"] == 503
            writer.close()
    finally:
        await server.stop()


async def test_a_flood_of_malformed_frames_closes_the_session() -> None:
    server = NodeServer(node=NODE, handle=fake_backend, authenticate=accept_token, max_malformed=2)
    await server.start("127.0.0.1", 0)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port, limit=MAX_FRAME)
        hello = {"type": "hello", "matp": "0.1", "agent": CALLER.model_dump(), "token": "s3cret"}
        writer.write(encode(hello))
        await read_json(reader)
        writer.write(b"bad\n" * 3 + encode({"id": "r-1", "intent": "PING"}))

        # The third malformed frame goes past max_malformed=2: the PING is never read.
        replies = [await read_json(reader) for _ in range(3)]
        assert [r["status"] for r in replies] == [400, 400, 400]
        assert await asyncio.wait_for(reader.read(), 2) == b""
        writer.close()
    finally:
        await server.stop()


async def test_a_cancelled_request_leaves_nothing_pending(node: NodeServer) -> None:
    async with client_for(node) as client:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(client.request("PING", params={"delay": 1}), 0.1)

        assert client._pending == {}


async def test_a_read_survives_a_node_restart_but_a_write_is_not_resent() -> None:
    server = NodeServer(node=NODE, handle=fake_backend, authenticate=accept_token)
    await server.start("127.0.0.1", 0)
    port = server.port
    client = MatpClient("127.0.0.1", port, CALLER, token="s3cret")
    try:
        read = asyncio.create_task(client.request("PING", params={"delay": 0.5}))
        write = asyncio.create_task(client.request("BOOK", "le-petit-zinc", {"delay": 0.5}))
        await asyncio.sleep(0.1)
        await server.stop()
        server = NodeServer(node=NODE, handle=fake_backend, authenticate=accept_token)
        await server.start("127.0.0.1", port)

        assert (await read)["pong"] is True
        with pytest.raises(AgentUnavailable, match="closed before the answer"):
            await write
    finally:
        await client.close()
        await server.stop()
