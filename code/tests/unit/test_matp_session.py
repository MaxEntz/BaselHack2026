"""MATP sessions over real TCP on loopback, with a fake backend (PROTOCOL.md §3, §5, §6, §10)."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest

from core.errors import AgentUnavailable, InvalidInput, SlotUnavailable
from matp.client import MatpClient, MatpRejected
from matp.codec import MAX_FRAME, encode, read_frame
from matp.messages import IN_FLIGHT_LIMIT, AgentIdentity, Hello, NodeIdentity, Request
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


Script = Callable[[list[dict[str, Any]]], Awaitable[list[dict[str, Any]]]]


@asynccontextmanager
async def scripted_node(script: Script) -> AsyncIterator[tuple[int, list[str]]]:
    """A node that welcomes anyone, then sends whatever `script` returns for the requests so far.

    Yields its port and the ids of the sessions it opened, to check that none was reopened.
    """
    sessions: list[str] = []

    async def session(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        sessions.append(f"s-{len(sessions) + 1}")
        await read_frame(reader)
        welcome = {"node": NODE.model_dump(), "intents": ["PING", "BOOK"], "session": sessions[-1]}
        writer.write(encode({"type": "welcome", "matp": "0.1", **welcome}))
        requests: list[dict[str, Any]] = []
        while (frame := await read_frame(reader)) is not None:
            requests.append(frame)
            for answer in await script(requests):
                writer.write(encode(answer))
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(session, "127.0.0.1", 0, limit=MAX_FRAME)
    try:
        yield server.sockets[0].getsockname()[1], sessions
    finally:
        server.close()


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


async def test_an_id_already_in_flight_is_refused(node: NodeServer) -> None:
    async with client_for(node) as client:
        first = asyncio.create_task(
            client.request("PING", params={"delay": 0.2}, request_id="r-same")
        )
        await asyncio.sleep(0.05)

        with pytest.raises(ValueError, match="r-same"):
            await client.request("PING", request_id="r-same")

        assert (await first)["pong"] is True


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


async def test_requests_beyond_the_in_flight_limit_get_429_without_being_run() -> None:
    server = NodeServer(node=NODE, handle=fake_backend, authenticate=accept_token, max_in_flight=1)
    await server.start("127.0.0.1", 0)
    try:
        reader, writer = await raw_session(server)
        hello = {"type": "hello", "matp": "0.1", "agent": CALLER.model_dump(), "token": "s3cret"}
        writer.write(encode(hello))
        await read_json(reader)
        slow = {"type": "request", "id": "slow", "intent": "PING", "params": {"delay": 0.3}}
        writer.write(encode(slow) + encode({"type": "request", "id": "over", "intent": "PING"}))
        await writer.drain()

        over = await read_json(reader)
        assert over["id"] == "over"
        assert over["status"] == 429
        assert over["error"]["code"] == "too_many_requests"
        assert (await read_json(reader))["id"] == "slow"
        writer.close()
    finally:
        await server.stop()


async def test_a_429_is_agent_unavailable_for_the_caller() -> None:
    server = NodeServer(node=NODE, handle=fake_backend, authenticate=accept_token, max_in_flight=1)
    await server.start("127.0.0.1", 0)
    try:
        async with MatpClient("127.0.0.1", server.port, CALLER, token="s3cret") as client:
            slow = asyncio.create_task(client.request("PING", params={"delay": 0.3}))
            await asyncio.sleep(0.05)
            with pytest.raises(AgentUnavailable, match="in flight"):
                await client.request("PING")
            assert (await slow)["pong"] is True
    finally:
        await server.stop()


async def test_the_client_stays_under_the_node_s_in_flight_limit(node: NodeServer) -> None:
    async with client_for(node) as client:
        pings = [client.request("PING", params={"delay": 0.2}) for _ in range(IN_FLIGHT_LIMIT + 8)]
        answers = await asyncio.gather(*pings)

    assert all(answer["pong"] is True for answer in answers)


async def test_a_429_on_one_request_leaves_the_others_on_the_session_answered() -> None:
    async def script(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # BOOK is held until PING arrives; PING gets the draft's 429, then BOOK its answer.
        if len(requests) < 2:
            return []
        book, ping = requests
        return [
            {
                "type": "response",
                "id": ping["id"],
                "status": 429,
                "error": {"code": "too_many_requests", "message": "Too many requests in flight"},
            },
            {"type": "response", "id": book["id"], "status": 200, "result": {"booked": True}},
        ]

    async with (
        asyncio.timeout(5),  # a regression must fail, not hang
        scripted_node(script) as (port, sessions),
        MatpClient("127.0.0.1", port, CALLER) as client,
    ):
        book = asyncio.create_task(client.request("BOOK", "le-petit-zinc"))
        await asyncio.sleep(0.05)
        with pytest.raises(AgentUnavailable):
            await client.request("PING")
        assert await book == {"booked": True}

    assert sessions == ["s-1"]


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (418, {"code": "teapot", "message": "I'm a teapot"}),
        (599, None),
        ("not-a-status", None),
    ],
)
async def test_an_unknown_or_invalid_answer_fails_only_its_request(
    status: object, error: dict[str, Any] | None
) -> None:
    async def script(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        request = requests[-1]
        if request["intent"] == "PING":
            return [{"type": "response", "id": request["id"], "status": 200, "result": {}}]
        return [{"type": "response", "id": request["id"], "status": status, "error": error}]

    async with (
        asyncio.timeout(5),  # a regression must fail, not hang
        scripted_node(script) as (port, sessions),
        MatpClient("127.0.0.1", port, CALLER) as client,
    ):
        with pytest.raises(AgentUnavailable):
            await client.request("BOOK", "le-petit-zinc")
        assert await client.request("PING") == {}

    assert sessions == ["s-1"]


async def test_an_unreadable_answer_without_an_id_drops_the_session() -> None:
    # Nobody can tell whose answer it was: every request on the session fails, none hangs.
    async def script(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"type": "response", "status": "garbled"}]

    async with (
        asyncio.timeout(5),  # a regression must fail, not hang
        scripted_node(script) as (port, _),
        MatpClient("127.0.0.1", port, CALLER) as client,
    ):
        with pytest.raises(AgentUnavailable, match="closed before the answer"):
            await client.request("BOOK", "le-petit-zinc")


async def test_any_2xx_status_is_a_success() -> None:
    async def script(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        request = requests[-1]
        return [{"type": "response", "id": request["id"], "status": 202, "result": {"ok": 1}}]

    async with (
        asyncio.timeout(5),  # a regression must fail, not hang
        scripted_node(script) as (port, _),
        MatpClient("127.0.0.1", port, CALLER) as client,
    ):
        assert await client.request("PING") == {"ok": 1}


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


async def test_a_read_survives_a_dropped_session_but_a_write_is_not_resent(
    node: NodeServer,
) -> None:
    client = client_for(node)
    try:
        read = asyncio.create_task(client.request("PING", params={"delay": 0.5}))
        write = asyncio.create_task(client.request("BOOK", "le-petit-zinc", {"delay": 0.5}))
        await asyncio.sleep(0.1)
        for writer in list(node._writers):  # the session drops, the node keeps listening
            writer.close()

        assert (await read)["pong"] is True
        with pytest.raises(AgentUnavailable, match="closed before the answer"):
            await write
    finally:
        await client.close()


async def test_a_slow_answer_is_not_cut_by_the_idle_timeout() -> None:
    server = NodeServer(node=NODE, handle=fake_backend, authenticate=accept_token, idle_timeout=0.1)
    await server.start("127.0.0.1", 0)
    try:
        async with MatpClient("127.0.0.1", server.port, CALLER, token="s3cret") as client:
            assert (await client.request("PING", params={"delay": 0.4}))["pong"] is True
    finally:
        await server.stop()
