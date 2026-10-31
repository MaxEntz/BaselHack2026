"""The node's request log and the pause switch (#146), on a real Postgres 16."""

import asyncio
import json
import time as clock
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.service import PgAgentService
from batp.client import BatpClient
from batp.messages import AgentIdentity, Hello, Request, Response
from batp.server import NodeServer, Recorder
from core.db import agent_requests_t
from core.errors import AgentUnavailable, InvalidInput, NotFound, PartyTooLarge
from merchant_node.__main__ import NODE
from merchant_node.agents import MerchantAgents
from merchant_node.requests_log import RequestLog, log_engine

pytestmark = pytest.mark.integration

MerchantFactory = Callable[..., Awaitable[str]]  # the `make_merchant` fixture
NodeFactory = Callable[[Recorder | None], Awaitable[BatpClient]]

TZ = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 10, 31, 9, tzinfo=TZ)
DAY = date(2026, 11, 2)
GUEST = "Ada Lovelace"
TOKEN = "gw-secret-token-1234"
GATEWAY = AgentIdentity(
    id="urn:batp:agent:claude-gateway",
    name="Claude via the Sésame gateway",
    kind="user-agent",
    operator="merchant-agents",
)


def at(hhmm: str) -> str:
    return datetime.combine(DAY, time.fromisoformat(hhmm), tzinfo=TZ).isoformat()


@pytest.fixture
def service(engine: AsyncEngine) -> PgAgentService:
    return PgAgentService(engine, tz=TZ, clock=lambda: NOW)


@pytest.fixture
async def connect(engine: AsyncEngine, service: PgAgentService) -> AsyncIterator[NodeFactory]:
    """A node with the given recorder, and a client signed in as the gateway."""
    nodes: list[NodeServer] = []
    clients: list[BatpClient] = []

    def only_the_gateway(hello: Hello) -> bool:
        return hello.agent.id == GATEWAY.id and hello.token == TOKEN

    async def start(record: Recorder | None) -> BatpClient:
        agents = MerchantAgents(engine, service)
        node = NodeServer(
            node=NODE,
            handle=agents,
            authenticate=only_the_gateway,
            fallback_url=agents.fallback_url,
            record=record,
        )
        await node.start("127.0.0.1", 0)
        nodes.append(node)
        client = BatpClient("127.0.0.1", node.port, GATEWAY, token=TOKEN)
        await client.__aenter__()
        clients.append(client)
        return client

    yield start
    for client in clients:
        await client.__aexit__(None, None, None)
    for node in nodes:
        await node.stop()


async def rows(engine: AsyncEngine, slug: str) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        result = await conn.execute(
            select(agent_requests_t)
            .where(agent_requests_t.c.merchant_slug == slug)
            .order_by(agent_requests_t.c.id)
        )
        return [dict(row) for row in result.mappings()]


async def settled(engine: AsyncEngine, slug: str, count: int) -> list[dict[str, Any]]:
    """The merchant's rows once `count` are there: the recorder runs after each answer."""
    for _ in range(100):
        found = await rows(engine, slug)
        if len(found) >= count:
            return found
        await asyncio.sleep(0.02)
    return await rows(engine, slug)


async def test_a_booking_is_logged_with_both_frames_and_never_the_token(
    engine: AsyncEngine, connect: NodeFactory, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    client = await connect(RequestLog(engine))
    booking = {
        "starts_at": at("20:00"),
        "party_size": 2,
        "guest_name": GUEST,
        "idempotency_key": str(uuid4()),
    }

    booked = await client.request("BOOK", slug, booking)

    [row] = await settled(engine, slug, 1)
    assert (row["agent_name"], row["intent"], row["status"]) == (GATEWAY.name, "BOOK", 200)
    assert row["request"]["intent"] == "BOOK"
    assert row["request"]["merchant"] == slug
    assert row["request"]["params"] == booking
    assert row["response"]["status"] == 200
    assert row["response"]["result"] == booked
    stored = json.dumps([row["request"], row["response"]])
    assert TOKEN not in stored
    assert "hello" not in stored


async def test_a_refusal_is_logged_with_its_status_and_fallback_url(
    engine: AsyncEngine, connect: NodeFactory, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(tables=[{"label": "T1", "seats": 4}], seats=4)
    client = await connect(RequestLog(engine))

    with pytest.raises(PartyTooLarge):
        await client.request("AVAILABILITY", slug, {"date": DAY.isoformat(), "party_size": 12})

    [row] = await settled(engine, slug, 1)
    assert row["status"] == 413
    assert row["response"]["error"]["code"] == "party_too_large"
    assert row["response"]["error"]["fallback_url"]


async def test_requests_without_a_published_merchant_leave_no_row(
    engine: AsyncEngine, connect: NodeFactory, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    building = await make_merchant(live=False)
    client = await connect(RequestLog(engine))

    await client.request("PING")
    await client.request("DISCOVER", params={"query": "zinc"})
    await client.request("RECALL", params={"guest_name": GUEST})
    with pytest.raises(NotFound):
        await client.request(
            "AVAILABILITY", "chez-personne", {"date": DAY.isoformat(), "party_size": 2}
        )
    with pytest.raises(NotFound):
        await client.request("AVAILABILITY", building, {"date": DAY.isoformat(), "party_size": 2})
    await client.request("AVAILABILITY", slug, {"date": DAY.isoformat(), "party_size": 2})

    found = await settled(engine, slug, 1)
    async with engine.connect() as conn:
        unknown = await conn.scalar(
            select(func.count())
            .select_from(agent_requests_t)
            .where(agent_requests_t.c.merchant_slug == "chez-personne")
        )
    assert [row["intent"] for row in found] == ["AVAILABILITY"]
    assert unknown == 0
    assert await rows(engine, building) == []


async def test_rows_older_than_7_days_are_purged_as_new_ones_arrive(
    engine: AsyncEngine, connect: NodeFactory, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    async with engine.begin() as conn:
        await conn.execute(
            insert(agent_requests_t).values(
                merchant_slug=slug,
                at=func.now() - timedelta(days=8),
                agent_name="Old agent",
                intent="PING",
                status=200,
                request={},
                response={},
            )
        )
    client = await connect(RequestLog(engine))

    await client.request("AVAILABILITY", slug, {"date": DAY.isoformat(), "party_size": 2})

    for _ in range(100):
        found = await rows(engine, slug)
        if [row["agent_name"] for row in found] == [GATEWAY.name]:
            break
        await asyncio.sleep(0.02)
    assert [row["agent_name"] for row in found] == [GATEWAY.name]


async def test_a_failing_or_slow_log_never_delays_or_fails_the_answer(
    connect: NodeFactory, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    calls: list[str] = []

    async def broken(agent: AgentIdentity, request: Request, response: Response) -> None:
        calls.append(request.intent)
        raise RuntimeError("the log is down")

    async def slow(agent: AgentIdentity, request: Request, response: Response) -> None:
        await asyncio.sleep(3)

    params = {"date": DAY.isoformat(), "party_size": 2}
    failing = await connect(broken)
    assert (await failing.request("AVAILABILITY", slug, params))["slots"]
    assert (await failing.request("AVAILABILITY", slug, params))["slots"]

    lagging = await connect(slow)
    started = clock.monotonic()
    assert (await lagging.request("AVAILABILITY", slug, params))["slots"]
    assert clock.monotonic() - started < 1

    await asyncio.sleep(0.05)
    assert calls == ["AVAILABILITY", "AVAILABILITY"]


async def test_the_log_has_its_own_small_pool_and_drops_records_when_full(
    engine: AsyncEngine,
    connect: NodeFactory,
    make_merchant: MerchantFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    slug = await make_merchant()
    own = log_engine(engine.url.render_as_string(hide_password=False))
    try:
        assert (own.pool.size(), own.pool._max_overflow) == (1, 1)  # type: ignore[attr-defined]
        client = await connect(RequestLog(own, max_pending=0))

        answered = await client.request(
            "AVAILABILITY", slug, {"date": DAY.isoformat(), "party_size": 2}
        )
        await asyncio.sleep(0.2)
    finally:
        await own.dispose()

    assert answered["slots"]
    assert await rows(engine, slug) == []
    assert "request log full: record dropped" in caplog.text


async def test_pause_sends_agents_to_the_booking_page_and_resume_brings_slots_back(
    engine: AsyncEngine,
    service: PgAgentService,
    connect: NodeFactory,
    make_merchant: MerchantFactory,
) -> None:
    slug = await make_merchant()
    client = await connect(RequestLog(engine))
    params = {"date": DAY.isoformat(), "time": "20:00", "party_size": 2}

    await service.set_status(slug, "down")
    with pytest.raises(AgentUnavailable):
        await client.request("AVAILABILITY", slug, params)
    await service.set_status(slug, "live")
    resumed = await client.request("AVAILABILITY", slug, params)

    paused, live = await settled(engine, slug, 2)
    assert paused["status"] == 503
    assert paused["response"]["error"]["fallback_url"] == "http://localhost:8080/reservation.html"
    assert live["status"] == 200
    assert resumed["slots"]


async def test_only_a_published_agent_can_be_paused_or_resumed(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    building = await make_merchant(live=False)

    with pytest.raises(InvalidInput):
        await service.set_status(building, "live")
    with pytest.raises(NotFound):
        await service.set_status("chez-personne", "down")
