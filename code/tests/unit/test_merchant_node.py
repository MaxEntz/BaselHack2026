"""Who gets into the node (PROTOCOL.md §5, §10): tokens, and loopback-only without them; and
how `run` stops it on a signal."""

import asyncio
import os
import signal
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from batp.client import BatpClient
from batp.messages import AgentIdentity, Hello, Request, Response
from batp.server import NodeServer
from core.config import Settings
from merchant_node import __main__ as node_main

GATEWAY = AgentIdentity(
    id="urn:batp:agent:claude-gateway", name="Gateway", kind="user-agent", operator="team"
)
STRANGER = AgentIdentity(
    id="urn:batp:agent:stranger", name="Stranger", kind="user-agent", operator="nobody"
)


def settings(**values: Any) -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://u:p@localhost/db", **values)


@pytest.mark.parametrize(
    ("agent", "token", "allowed"),
    [
        (GATEWAY, "gw-token-0123456789", True),
        (GATEWAY, "wrong", False),
        (GATEWAY, None, False),
        (STRANGER, "gw-token-0123456789", False),
    ],
)
def test_with_tokens_only_listed_agents_with_their_token_get_in(
    agent: AgentIdentity, token: str | None, allowed: bool
) -> None:
    check = node_main.token_check(settings(batp_tokens={GATEWAY.id: "gw-token-0123456789"}))

    assert check(Hello(batp="0.1", agent=agent, token=token)) is allowed


def test_without_tokens_any_agent_gets_in() -> None:
    check = node_main.token_check(settings())

    assert check(Hello(batp="0.1", agent=STRANGER)) is True


@pytest.mark.parametrize(
    ("values", "problems"),
    [
        ({}, []),
        ({"batp_host": "::1"}, []),
        ({"batp_host": "0.0.0.0"}, ["BATP_TOKENS is required", "BATP_TLS_CERT"]),
        ({"batp_host": "127.0.0.2"}, ["BATP_TOKENS is required", "BATP_TLS_CERT"]),
        (
            {
                "batp_host": "0.0.0.0",
                "batp_tokens": {GATEWAY.id: "a-long-enough-token"},
                "batp_tls_cert": "cert.pem",
                "batp_tls_key": "key.pem",
            },
            [],
        ),
        ({"batp_tokens": {GATEWAY.id: "short"}}, ["at least 16 characters"]),
        ({"batp_tokens": {GATEWAY.id: ""}}, ["at least 16 characters"]),
    ],
    ids=["loopback", "ipv6-loopback", "any", "other-127", "exposed-ok", "short", "empty"],
)
def test_exposure_problems(values: dict[str, Any], problems: list[str]) -> None:
    found = node_main.exposure_problems(settings(**values))

    assert len(found) == len(problems)
    for expected, actual in zip(problems, found, strict=True):
        assert expected in actual


async def listening(server: NodeServer) -> int:
    """The node's port, once `run` has started it."""
    for _ in range(500):
        try:
            return server.port
        except RuntimeError:
            await asyncio.sleep(0.01)
    raise AssertionError("the node never started listening")


@pytest.mark.parametrize("signum", node_main.STOP_SIGNALS, ids=["SIGTERM", "SIGINT"])
async def test_a_stop_signal_drains_the_node_and_disposes_its_engines(signum: int) -> None:
    recording = asyncio.Event()
    recorded: list[str] = []

    async def handle(_: AgentIdentity, request: Request) -> dict[str, Any]:
        return {"pong": True}

    async def record(_: AgentIdentity, request: Request, response: Response) -> None:
        recording.set()
        await asyncio.sleep(0.2)  # still writing its row when the signal comes
        recorded.append(request.id)

    server = NodeServer(
        node=node_main.NODE, handle=handle, authenticate=lambda _: True, record=record
    )
    engines = [AsyncMock(spec=AsyncEngine), AsyncMock(spec=AsyncEngine)]
    running = asyncio.create_task(node_main.run(server, "127.0.0.1", 0, engines=engines))
    async with BatpClient("127.0.0.1", await listening(server), GATEWAY) as client:
        assert await client.request("PING", request_id="r-last") == {"pong": True}
    await asyncio.wait_for(recording.wait(), 2)

    os.kill(os.getpid(), signum)
    await asyncio.wait_for(running, 5)

    assert recorded == ["r-last"]  # the recorder finished before the node stopped
    for engine in engines:
        engine.dispose.assert_awaited_once()
    with pytest.raises(RuntimeError, match="not listening"):
        _ = server.port


async def test_a_second_signal_while_draining_changes_nothing() -> None:
    # Were `run` to remove its handlers before draining, the default ones would be back: a
    # second SIGTERM would kill the process, a second SIGINT raise KeyboardInterrupt, both
    # before the engines close.
    started = asyncio.Event()

    async def handle(_: AgentIdentity, request: Request) -> dict[str, Any]:
        started.set()
        await asyncio.sleep(0.5)
        return {"pong": True}

    server = NodeServer(node=node_main.NODE, handle=handle, authenticate=lambda _: True)
    engines = [AsyncMock(spec=AsyncEngine)]
    running = asyncio.create_task(node_main.run(server, "127.0.0.1", 0, engines=engines))
    async with BatpClient("127.0.0.1", await listening(server), GATEWAY) as client:
        answer = asyncio.create_task(client.request("PING"))
        await asyncio.wait_for(started.wait(), 2)
        # One signal to stop, then one of each while the node drains.
        for signum in (signal.SIGTERM, signal.SIGTERM, signal.SIGINT):
            os.kill(os.getpid(), signum)
            await asyncio.sleep(0.05)

        assert await answer == {"pong": True}  # in flight: answered within the grace
    await asyncio.wait_for(running, 5)
    engines[0].dispose.assert_awaited_once()


async def test_the_engines_are_disposed_even_when_stopping_fails() -> None:
    server = AsyncMock(spec=NodeServer)
    server.stop.side_effect = RuntimeError("stop failed")
    engines = [AsyncMock(spec=AsyncEngine), AsyncMock(spec=AsyncEngine)]
    running = asyncio.create_task(node_main.run(server, "127.0.0.1", 0, engines=engines))
    await asyncio.sleep(0.05)
    os.kill(os.getpid(), signal.SIGTERM)

    with pytest.raises(RuntimeError, match="stop failed"):
        await asyncio.wait_for(running, 5)
    for engine in engines:
        engine.dispose.assert_awaited_once()
