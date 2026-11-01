"""BATP sessions over real TCP on loopback, with a fake backend (PROTOCOL.md §3, §5, §6, §10)."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from pydantic import BaseModel

from batp.client import BatpClient, BatpRejected, SessionLost
from batp.codec import MAX_FRAME, encode, read_frame
from batp.messages import IN_FLIGHT_LIMIT, AgentIdentity, Hello, NodeIdentity, Request, Response
from batp.server import NodeServer
from core.errors import AgentUnavailable, InvalidInput, SlotUnavailable

CALLER = AgentIdentity(
    id="urn:batp:agent:test", name="Test agent", kind="user-agent", operator="tests"
)
NODE = NodeIdentity(id="urn:batp:node:test", name="Test node")


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


class Held:
    """`fake_backend`, except that a request with `hold` in its params waits for `release`.

    `started` is set once `expected` held requests are running on the node: a test waits for
    it to know they are in flight, rather than sleeping and hoping.
    """

    def __init__(self, expected: int = 1) -> None:
        self.expected = expected
        self.running = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, agent: AgentIdentity, request: Request) -> dict[str, Any]:
        if not request.params.get("hold"):
            return await fake_backend(agent, request)
        self.running += 1
        if self.running >= self.expected:
            self.started.set()
        await self.release.wait()
        if request.intent == "BOOK":
            return {"booked": True}
        return {"pong": True, "agent": agent.id}


def accept_token(hello: Hello) -> bool:
    return hello.token == "s3cret"


@pytest.fixture
def held() -> Held:
    return Held()


@pytest.fixture
async def node(held: Held) -> AsyncIterator[NodeServer]:
    server = NodeServer(node=NODE, handle=held, authenticate=accept_token)
    await server.start("127.0.0.1", 0)
    yield server
    await server.stop()


def client_for(node: NodeServer, token: str | None = "s3cret") -> BatpClient:
    return BatpClient("127.0.0.1", node.port, CALLER, token=token)


async def raw_session(node: NodeServer) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection("127.0.0.1", node.port, limit=MAX_FRAME)


async def read_json(reader: asyncio.StreamReader) -> dict[str, Any]:
    line = await asyncio.wait_for(reader.readline(), 2)
    frame: dict[str, Any] = json.loads(line)
    return frame


# The answers to send for the requests so far, or None to drop the session.
Script = Callable[[list[dict[str, Any]]], Awaitable[list[dict[str, Any]] | None]]


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
        writer.write(encode({"type": "welcome", "batp": "0.1", **welcome}))
        requests: list[dict[str, Any]] = []
        while (frame := await read_frame(reader)) is not None:
            requests.append(frame)
            if (answers := await script(requests)) is None:
                break
            for answer in answers:
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

        assert result == {"pong": True, "agent": "urn:batp:agent:test"}
        assert client.welcome is not None
        assert "PING" in client.welcome.intents
        assert client.welcome.node == NODE


async def test_the_caller_can_name_its_request(
    node: NodeServer, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="batp"):
        async with client_for(node) as client:
            assert await client.request("PING", request_id="r-mine") == {
                "pong": True,
                "agent": CALLER.id,
            }

    lines = [r.__dict__ for r in caplog.records if r.getMessage() == "batp request"]
    assert [line["request"] for line in lines] == ["r-mine"]


async def test_an_id_already_in_flight_is_refused(node: NodeServer, held: Held) -> None:
    async with (
        asyncio.timeout(5),  # a regression must fail, not hang
        client_for(node) as client,
    ):
        first = asyncio.create_task(
            client.request("PING", params={"hold": True}, request_id="r-same")
        )
        await held.started.wait()

        with pytest.raises(ValueError, match="r-same"):
            await client.request("PING", request_id="r-same")

        held.release.set()
        assert (await first)["pong"] is True


async def test_a_wrong_token_is_rejected_with_401(node: NodeServer) -> None:
    with pytest.raises(BatpRejected) as rejected:
        async with client_for(node, token="wrong") as client:
            await client.request("PING")

    assert rejected.value.status == 401


async def test_another_major_version_is_rejected_with_426(node: NodeServer) -> None:
    reader, writer = await raw_session(node)
    hello = {"type": "hello", "batp": "1.0", "agent": CALLER.model_dump(), "token": "s3cret"}
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
    hello = {"type": "hello", "batp": "0.1", "agent": CALLER.model_dump(), "token": "s3cret"}
    writer.write(encode(hello))
    assert (await read_json(reader))["type"] == "welcome"

    writer.write(encode({"type": "request", "id": "r-1", "intent": "STEAL", "params": {}}))
    writer.write(b"this is not json\n")
    writer.write(encode({"type": "request", "id": "r-2", "intent": "PING", "params": {}}))

    replies = [await read_json(reader) for _ in range(3)]
    assert [(r["type"], r.get("id"), r["status"]) for r in replies] == [
        ("response", "r-1", 400),
        ("error", None, 400),
        ("response", "r-2", 200),
    ]
    writer.close()


async def test_an_oversized_frame_gets_413_and_closes_the_session(node: NodeServer) -> None:
    reader, writer = await raw_session(node)
    hello = {"type": "hello", "batp": "0.1", "agent": CALLER.model_dump(), "token": "s3cret"}
    writer.write(encode(hello))
    await read_json(reader)

    writer.write(b'{"type": "request", "id": "big", "params": "' + b"a" * MAX_FRAME + b'"}\n')
    await writer.drain()

    error = await read_json(reader)
    assert (error["type"], error["status"]) == ("error", 413)
    assert await asyncio.wait_for(reader.read(), 2) == b""
    writer.close()


async def test_requests_beyond_the_in_flight_limit_get_429_without_being_run() -> None:
    server = NodeServer(node=NODE, handle=fake_backend, authenticate=accept_token, max_in_flight=1)
    await server.start("127.0.0.1", 0)
    try:
        reader, writer = await raw_session(server)
        hello = {"type": "hello", "batp": "0.1", "agent": CALLER.model_dump(), "token": "s3cret"}
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


async def test_a_429_is_agent_unavailable_for_the_caller(held: Held) -> None:
    server = NodeServer(node=NODE, handle=held, authenticate=accept_token, max_in_flight=1)
    await server.start("127.0.0.1", 0)
    try:
        async with (
            asyncio.timeout(5),  # a regression must fail, not hang
            BatpClient("127.0.0.1", server.port, CALLER, token="s3cret") as client,
        ):
            slow = asyncio.create_task(client.request("PING", params={"hold": True}))
            await held.started.wait()
            with pytest.raises(AgentUnavailable, match="in flight"):
                await client.request("PING")
            held.release.set()
            assert (await slow)["pong"] is True
    finally:
        await server.stop()


async def test_the_client_stays_under_the_node_s_in_flight_limit(node: NodeServer) -> None:
    async with client_for(node) as client:
        pings = [client.request("PING", params={"delay": 0.2}) for _ in range(IN_FLIGHT_LIMIT + 8)]
        answers = await asyncio.gather(*pings)

    assert all(answer["pong"] is True for answer in answers)


async def test_a_429_on_one_request_leaves_the_others_on_the_session_answered() -> None:
    book_arrived = asyncio.Event()

    async def script(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # BOOK is held until PING arrives; PING gets the draft's 429, then BOOK its answer.
        if len(requests) < 2:
            book_arrived.set()
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
        BatpClient("127.0.0.1", port, CALLER) as client,
    ):
        book = asyncio.create_task(client.request("BOOK", "le-petit-zinc"))
        await book_arrived.wait()
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
        BatpClient("127.0.0.1", port, CALLER) as client,
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
        BatpClient("127.0.0.1", port, CALLER) as client,
    ):
        with pytest.raises(AgentUnavailable, match="closed before the answer"):
            await client.request("BOOK", "le-petit-zinc")


async def test_a_recall_lost_with_its_session_is_sent_again() -> None:
    # Safe: a notice comes back for a day after it was first returned (#91).
    async def script(requests: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        if len(sessions) == 1:
            return None  # the first session dies under the RECALL
        request = requests[-1]
        return [{"type": "response", "id": request["id"], "status": 200, "result": {"n": 1}}]

    async with (
        asyncio.timeout(5),  # a regression must fail, not hang
        scripted_node(script) as (port, sessions),
        BatpClient("127.0.0.1", port, CALLER) as client,
    ):
        assert await client.request("RECALL", params={"guest_name": "Ada"}) == {"n": 1}

    assert sessions == ["s-1", "s-2"]


async def test_any_2xx_status_is_a_success() -> None:
    async def script(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        request = requests[-1]
        return [{"type": "response", "id": request["id"], "status": 202, "result": {"ok": 1}}]

    async with (
        asyncio.timeout(5),  # a regression must fail, not hang
        scripted_node(script) as (port, _),
        BatpClient("127.0.0.1", port, CALLER) as client,
    ):
        assert await client.request("PING") == {"ok": 1}


async def test_an_idle_session_is_closed() -> None:
    server = NodeServer(node=NODE, handle=fake_backend, authenticate=accept_token, idle_timeout=0.2)
    await server.start("127.0.0.1", 0)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port, limit=MAX_FRAME)
        hello = {"type": "hello", "batp": "0.1", "agent": CALLER.model_dump(), "token": "s3cret"}
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
        async with BatpClient("127.0.0.1", port, CALLER, token="s3cret") as client:
            await client.request("PING")


async def test_the_client_reconnects_once_after_the_node_restarts() -> None:
    server = NodeServer(node=NODE, handle=fake_backend, authenticate=accept_token)
    await server.start("127.0.0.1", 0)
    port = server.port
    client = BatpClient("127.0.0.1", port, CALLER, token="s3cret")
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
        async with BatpClient("127.0.0.1", server.port, CALLER, token="s3cret") as client:
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


async def test_a_503_that_replaces_a_large_answer_is_recorded_when_the_session_is_gone(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # #220: the retry used to raise out of the task, so the request was never recorded and
    # asyncio logged "Task exception was never retrieved".
    recorded: list[tuple[str, int]] = []
    done = asyncio.Event()

    async def record(agent: AgentIdentity, request: Request, response: Response) -> None:
        recorded.append((request.id, response.status))
        done.set()

    async def send(frame: BaseModel) -> None:
        encode(frame)  # FrameTooLarge for RECALL's answer, as the session's own send
        raise ConnectionError("peer stopped reading")

    server = NodeServer(node=NODE, handle=fake_backend, authenticate=accept_token, record=record)
    request = Request(id="r-large", intent="RECALL")

    with caplog.at_level(logging.WARNING, logger="batp"):
        await server._answer((CALLER, "s-1"), request, send)
        await asyncio.wait_for(done.wait(), 2)

    assert recorded == [("r-large", 503)]
    [lost] = [r for r in caplog.records if r.getMessage().startswith("batp answer lost")]
    assert (lost.__dict__["request"], lost.__dict__["status"]) == ("r-large", 503)


async def test_an_answer_cancelled_mid_send_is_still_recorded_once() -> None:
    # The session's end cancels its tasks (#220): the request was carried out all the same.
    recorded: list[tuple[str, int]] = []
    done = asyncio.Event()
    sending = asyncio.Event()

    async def record(agent: AgentIdentity, request: Request, response: Response) -> None:
        recorded.append((request.id, response.status))
        done.set()

    async def send(frame: BaseModel) -> None:
        sending.set()
        await asyncio.Event().wait()  # a peer that stopped reading

    server = NodeServer(node=NODE, handle=fake_backend, authenticate=accept_token, record=record)
    answer = asyncio.create_task(
        server._answer((CALLER, "s-1"), Request(id="r-cut", intent="PING"), send)
    )
    await asyncio.wait_for(sending.wait(), 2)
    answer.cancel()

    with pytest.raises(asyncio.CancelledError):
        await answer
    await asyncio.wait_for(done.wait(), 2)
    await asyncio.sleep(0)
    assert recorded == [("r-cut", 200)]


async def test_an_unreadable_frame_from_the_node_is_logged_and_drops_the_session(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # #220: the client's reader used to end on it without a word.
    async def session(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await read_frame(reader)
        welcome = {"node": NODE.model_dump(), "intents": ["BOOK"], "session": "s-1"}
        writer.write(encode({"type": "welcome", "batp": "0.1", **welcome}))
        await read_frame(reader)
        writer.write(b"not json\n")
        await writer.drain()
        await reader.read()
        writer.close()

    server = await asyncio.start_server(session, "127.0.0.1", 0, limit=MAX_FRAME)
    try:
        port = server.sockets[0].getsockname()[1]
        with caplog.at_level(logging.WARNING, logger="batp"):
            async with BatpClient("127.0.0.1", port, CALLER) as client:
                with pytest.raises(SessionLost):
                    await client.request("BOOK", "le-petit-zinc", {})
    finally:
        server.close()

    [line] = [r for r in caplog.records if r.getMessage().startswith("batp frame unreadable")]
    assert line.levelno == logging.WARNING and line.exc_info


async def test_sessions_beyond_the_limit_are_refused() -> None:
    server = NodeServer(node=NODE, handle=fake_backend, authenticate=accept_token, max_sessions=1)
    await server.start("127.0.0.1", 0)
    try:
        async with BatpClient("127.0.0.1", server.port, CALLER, token="s3cret") as first:
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
        hello = {"type": "hello", "batp": "0.1", "agent": CALLER.model_dump(), "token": "s3cret"}
        writer.write(encode(hello))
        await read_json(reader)
        writer.write(b"bad\n" * 2 + encode({"id": "r-1", "intent": "PING"}))

        # The second malformed frame in a row reaches max_malformed=2 and closes the session
        # (PROTOCOL.md §10: "after 10 malformed frames in a row"): the PING is never read.
        replies = [await read_json(reader) for _ in range(2)]
        assert [(r["type"], r["status"]) for r in replies] == [("error", 400), ("error", 400)]
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
    node: NodeServer, held: Held
) -> None:
    held.expected = 2
    client = client_for(node)
    try:
        async with asyncio.timeout(5):  # a regression must fail, not hang
            read = asyncio.create_task(client.request("PING", params={"hold": True}))
            write = asyncio.create_task(client.request("BOOK", "le-petit-zinc", {"hold": True}))
            await held.started.wait()
            for writer in list(node._writers):  # the session drops, the node keeps listening
                writer.close()
            held.release.set()  # their answers go nowhere; the read's second try is answered

            assert (await read)["pong"] is True
            with pytest.raises(AgentUnavailable, match="closed before the answer"):
                await write
    finally:
        await client.close()


async def test_a_slow_answer_is_not_cut_by_the_idle_timeout() -> None:
    server = NodeServer(node=NODE, handle=fake_backend, authenticate=accept_token, idle_timeout=0.1)
    await server.start("127.0.0.1", 0)
    try:
        async with BatpClient("127.0.0.1", server.port, CALLER, token="s3cret") as client:
            assert (await client.request("PING", params={"delay": 0.4}))["pong"] is True
    finally:
        await server.stop()


# Where the node departed from PROTOCOL.md and the draft (#93).


async def welcomed(node: NodeServer) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await raw_session(node)
    hello = {"type": "hello", "batp": "0.1", "agent": CALLER.model_dump(), "token": "s3cret"}
    writer.write(encode(hello))
    assert (await read_json(reader))["type"] == "welcome"
    return reader, writer


async def test_an_id_in_flight_is_refused_without_being_run() -> None:
    calls: list[str] = []

    async def counting(agent: AgentIdentity, request: Request) -> dict[str, Any]:
        calls.append(request.id)
        return await fake_backend(agent, request)

    server = NodeServer(node=NODE, handle=counting, authenticate=accept_token)
    await server.start("127.0.0.1", 0)
    try:
        reader, writer = await welcomed(server)
        slow = {"type": "request", "id": "r-1", "intent": "PING", "params": {"delay": 0.3}}
        writer.write(encode(slow) + encode(slow))

        refused, answered = await read_json(reader), await read_json(reader)
        assert (refused["type"], refused["status"]) == ("error", 400)
        assert "r-1" in refused["message"]
        assert (answered["type"], answered["id"], answered["status"]) == ("response", "r-1", 200)
        # Once answered, the id may be used again.
        writer.write(encode({"type": "request", "id": "r-1", "intent": "PING", "params": {}}))
        assert (await read_json(reader))["status"] == 200
        assert calls == ["r-1", "r-1"]
        writer.close()
    finally:
        await server.stop()


@pytest.mark.parametrize(
    ("intent", "merchant", "status"),
    [
        ("PING", "le-petit-zinc", 400),
        ("DISCOVER", "le-petit-zinc", 400),
        ("RECALL", "le-petit-zinc", 400),
        ("BOOK", None, 400),
        ("PING", None, 200),
    ],
)
async def test_merchant_is_required_or_forbidden_by_intent(
    node: NodeServer, intent: str, merchant: str | None, status: int
) -> None:
    reader, writer = await welcomed(node)
    request = {"type": "request", "id": "r-1", "intent": intent, "params": {}}
    writer.write(encode(request | ({"merchant": merchant} if merchant else {})))

    response = await read_json(reader)
    assert (response["id"], response["status"]) == ("r-1", status)
    if status == 400:
        assert response["error"]["code"] == "invalid_input"
    writer.close()


async def test_the_tenth_malformed_frame_in_a_row_closes_the_session(node: NodeServer) -> None:
    reader, writer = await welcomed(node)
    # Nine are tolerated: a valid request resets the count.
    writer.write(b"bad\n" * 9 + encode({"type": "request", "id": "r-1", "intent": "PING"}))
    replies = [await read_json(reader) for _ in range(10)]
    assert replies[-1]["status"] == 200

    writer.write(b"bad\n" * 10 + encode({"type": "request", "id": "r-2", "intent": "PING"}))
    assert [(await read_json(reader))["type"] for _ in range(10)] == ["error"] * 10
    assert await asyncio.wait_for(reader.read(), 2) == b""
    writer.close()


@pytest.mark.parametrize(
    "frame",
    [
        b"not json\n",
        encode({"type": "surprise", "id": "r-1"}),
        encode({"type": "request", "intent": "PING"}),
        encode({"type": "request", "id": "x" * 65, "intent": "PING"}),
    ],
    ids=["not JSON", "unknown type", "no id", "invalid id"],
)
async def test_a_frame_without_a_request_id_gets_an_error_frame(
    node: NodeServer, frame: bytes
) -> None:
    reader, writer = await welcomed(node)
    writer.write(frame + encode({"type": "request", "id": "r-2", "intent": "PING"}))

    error, after = await read_json(reader), await read_json(reader)
    assert error == {"type": "error", "status": 400, "message": error["message"]}
    assert (after["id"], after["status"]) == ("r-2", 200)
    writer.close()


@pytest.mark.parametrize("intent", ["BOOK", "AMEND"])
async def test_a_503_from_the_node_carries_the_merchants_fallback_url(intent: str) -> None:
    async def fallback_url(request: Request) -> str:
        return f"https://{request.merchant}.example/book"

    # BOOK with a delay times out, AMEND fails (fake_backend).
    server = NodeServer(
        node=NODE,
        handle=fake_backend,
        authenticate=accept_token,
        handler_timeout=0.05,
        fallback_url=fallback_url,
    )
    await server.start("127.0.0.1", 0)
    try:
        reader, writer = await welcomed(server)
        request = {"type": "request", "id": "r-1", "intent": intent, "merchant": "zinc"}
        writer.write(encode(request | {"params": {"delay": 1}}))

        response = await read_json(reader)
        assert response["status"] == 503
        assert response["error"]["fallback_url"] == "https://zinc.example/book"
        writer.close()
    finally:
        await server.stop()


async def test_a_broken_fallback_lookup_still_answers_503() -> None:
    async def broken(request: Request) -> str:
        raise RuntimeError("registry down")

    server = NodeServer(
        node=NODE, handle=fake_backend, authenticate=accept_token, fallback_url=broken
    )
    await server.start("127.0.0.1", 0)
    try:
        reader, writer = await welcomed(server)
        request = {"type": "request", "id": "r-1", "intent": "AMEND", "merchant": "zinc"}
        writer.write(encode(request | {"params": {}}))

        response = await read_json(reader)
        assert (response["status"], response["error"].get("fallback_url")) == (503, None)
        writer.close()
    finally:
        await server.stop()


async def test_the_client_logs_an_error_frame_from_the_node(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def script(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        error = {"type": "error", "status": 400, "message": "Request r-1 is in flight"}
        answer = {"type": "response", "id": requests[-1]["id"], "status": 200, "result": {}}
        return [error, answer]

    with caplog.at_level(logging.WARNING, logger="batp.client"):
        async with (
            scripted_node(script) as (port, _),
            BatpClient("127.0.0.1", port, CALLER) as client,
        ):
            assert await client.request("PING") == {}

    [record] = [r for r in caplog.records if r.getMessage() == "batp error frame"]
    assert (record.__dict__["status"], record.__dict__["detail"]) == (
        400,
        "Request r-1 is in flight",
    )
