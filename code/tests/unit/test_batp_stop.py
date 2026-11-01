"""`NodeServer.stop()` drains the node (#220): what is in flight still gets its answer and its
record, within the grace; nothing new gets in meanwhile."""

import asyncio
from typing import Any

import pytest

from batp.client import BatpClient, SessionLost
from batp.messages import AgentIdentity, NodeIdentity, Request, Response
from batp.server import NodeServer

CALLER = AgentIdentity(
    id="urn:batp:agent:test", name="Test agent", kind="user-agent", operator="tests"
)
NODE = NodeIdentity(id="urn:batp:node:test", name="Test node")


class Slow:
    """A handler that takes `seconds` to answer; `started` is set once a request runs."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.started = asyncio.Event()

    async def __call__(self, agent: AgentIdentity, request: Request) -> dict[str, Any]:
        self.started.set()
        await asyncio.sleep(self.seconds)
        return {"pong": True}


def node(handler: Slow, recorded: list[tuple[str, int]], **limits: float) -> NodeServer:
    async def record(_: AgentIdentity, request: Request, response: Response) -> None:
        recorded.append((request.id, response.status))

    return NodeServer(
        node=NODE, handle=handler, authenticate=lambda _: True, record=record, **limits
    )


async def test_an_answer_in_flight_when_the_node_stops_still_goes_out_and_is_recorded() -> None:
    handler, recorded = Slow(1.0), []
    server = node(handler, recorded)
    await server.start("127.0.0.1", 0)
    async with BatpClient("127.0.0.1", server.port, CALLER) as client:
        answer = asyncio.create_task(client.request("PING", request_id="r-slow"))
        await asyncio.wait_for(handler.started.wait(), 2)
        await asyncio.sleep(0.2)  # the review's case: SIGTERM 0.2 s into a 1 s handler

        await server.stop()

        assert await answer == {"pong": True}
    assert recorded == [("r-slow", 200)]
    with pytest.raises(RuntimeError, match="not listening"):
        _ = server.port


async def test_no_new_session_gets_in_while_the_node_drains() -> None:
    handler, recorded = Slow(0.5), []
    server = node(handler, recorded)
    await server.start("127.0.0.1", 0)
    port = server.port
    async with BatpClient("127.0.0.1", port, CALLER) as client:
        answer = asyncio.create_task(client.request("PING"))
        await asyncio.wait_for(handler.started.wait(), 2)
        stopping = asyncio.create_task(server.stop())
        await asyncio.sleep(0.05)

        with pytest.raises(OSError):
            await asyncio.open_connection("127.0.0.1", port)
        assert await answer == {"pong": True}
        await stopping


async def test_an_answer_past_the_grace_is_cut_and_the_node_still_stops() -> None:
    handler, recorded = Slow(30), []
    server = node(handler, recorded, stop_grace=0.2)
    await server.start("127.0.0.1", 0)
    async with BatpClient("127.0.0.1", server.port, CALLER) as client:
        answer = asyncio.create_task(client.request("PING"))
        await asyncio.wait_for(handler.started.wait(), 2)

        await asyncio.wait_for(server.stop(), 2)

        with pytest.raises(SessionLost):
            await answer
    assert recorded == []  # never answered: nothing was carried out to record
