"""The node's request log and the pause switch (#146), on a real Postgres 16."""

import asyncio
import json
import time as clock
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.service import PgAgentService
from batp.client import BatpClient
from batp.codec import encode
from batp.messages import AgentIdentity, Hello, Request, Response
from batp.server import NodeServer, Recorder
from core.config import Settings
from core.db import agent_requests_t
from core.errors import AgentUnavailable, InvalidInput, NotFound, PartyTooLarge
from core.models import AgentRequests
from merchant_node.__main__ import NODE
from merchant_node.agents import MerchantAgents
from merchant_node.requests_log import RequestLog, log_engine
from merchant_platform.api import create_app

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
    """The merchant's rows, oldest first, frames parsed (their text in `*_text`)."""
    async with engine.connect() as conn:
        result = await conn.execute(
            select(agent_requests_t)
            .where(agent_requests_t.c.merchant_slug == slug)
            .order_by(agent_requests_t.c.id)
        )
        return [
            dict(row)
            | {
                "request": json.loads(row["request"]),
                "response": json.loads(row["response"]),
                "request_text": row["request"],
                "response_text": row["response"],
            }
            for row in result.mappings()
        ]


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
    stored = row["request_text"] + row["response_text"]
    assert TOKEN not in stored
    assert "hello" not in stored
    # The text that was on the wire, never re-serialized by Postgres (#129).
    response = Response(id=row["response"]["id"], status=200, result=booked)
    assert row["response_text"] == encode(response).decode().removesuffix("\n")
    assert row["request_text"].startswith('{"type":"request","id":')


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
                request="{}",
                response="{}",
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


# The Agent page's routes (#146 part 2), on the real service and node.


@pytest.fixture
def page(service: PgAgentService) -> Callable[[str], httpx.AsyncClient]:
    """An HTTP client on the platform, signed in as the given merchant's owner."""

    def open_as(owner: str) -> httpx.AsyncClient:
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            database_url="postgresql+psycopg://unused@localhost/unused",
            platform_merchant=owner,
        )
        app = create_app(settings, agent=service)
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    return open_as


async def local_day(engine: AsyncEngine, slug: str) -> date:
    """The merchant's day of its latest row (the node writes `now()`)."""
    async with engine.connect() as conn:
        at = await conn.scalar(
            select(func.max(agent_requests_t.c.at)).where(agent_requests_t.c.merchant_slug == slug)
        )
    assert at is not None
    return at.astimezone(TZ).date()


async def test_the_agent_page_lists_the_days_requests_worded_newest_first(
    engine: AsyncEngine,
    connect: NodeFactory,
    make_merchant: MerchantFactory,
    page: Callable[[str], httpx.AsyncClient],
) -> None:
    slug = await make_merchant(max_party_size=6)
    client = await connect(RequestLog(engine))
    booking = {
        "starts_at": at("20:00"),
        "party_size": 2,
        "guest_name": GUEST,
        "idempotency_key": str(uuid4()),
    }
    await client.request("BOOK", slug, booking)
    await settled(engine, slug, 1)
    with pytest.raises(PartyTooLarge):
        await client.request("AVAILABILITY", slug, {"date": DAY.isoformat(), "party_size": 9})
    stored = await settled(engine, slug, 2)
    day = await local_day(engine, slug)

    async with page(slug) as http:
        everything = await http.get(f"/merchants/{slug}/agent/requests?date={day}")
        refused = await http.get(f"/merchants/{slug}/agent/requests?date={day}&refused=true")
        other_day = await http.get(
            f"/merchants/{slug}/agent/requests?date={day - timedelta(days=1)}"
        )

    assert everything.status_code == 200
    listed = AgentRequests.model_validate(everything.json()).requests
    assert [(r.intent, r.status, r.refused) for r in listed] == [
        ("AVAILABILITY", 413, True),
        ("BOOK", 200, False),
    ]
    too_many, booked = listed
    assert (too_many.title, too_many.text) == (
        "A group of 9 was sent to your booking page",
        "Your limit is 6, so the guest got your link instead.",
    )
    assert booked.title == f"{GUEST} booked through {GATEWAY.name}"
    assert booked.text.startswith("Monday 2 November, 20:00, 2 guests, table ")
    assert booked.agent_name == GATEWAY.name
    assert booked.at.utcoffset() == TZ.utcoffset(booked.at.replace(tzinfo=None))
    # The frames as stored: the wire's text, never re-serialized.
    assert (booked.request, booked.response) == (
        stored[0]["request_text"],
        stored[0]["response_text"],
    )
    assert TOKEN not in everything.text
    assert [r.intent for r in AgentRequests.model_validate(refused.json()).requests] == [
        "AVAILABILITY"
    ]
    assert other_day.json() == {"requests": []}


async def test_the_agent_page_shows_the_latest_10(
    engine: AsyncEngine,
    make_merchant: MerchantFactory,
    page: Callable[[str], httpx.AsyncClient],
) -> None:
    slug = await make_merchant()
    noon = datetime.combine(DAY, time(12), tzinfo=TZ)
    async with engine.begin() as conn:
        await conn.execute(
            insert(agent_requests_t),
            [
                {
                    "merchant_slug": slug,
                    "at": noon + timedelta(minutes=minute),
                    "agent_name": "Claude",
                    "intent": "PING",
                    "status": 200,
                    "request": "{}",
                    "response": "{}",
                }
                for minute in range(12)
            ],
        )

    async with page(slug) as http:
        answer = await http.get(f"/merchants/{slug}/agent/requests?date={DAY}")

    listed = AgentRequests.model_validate(answer.json()).requests
    assert [r.at for r in listed] == [noon + timedelta(minutes=m) for m in range(11, 1, -1)]


async def test_pause_and_resume_from_the_agent_page(
    engine: AsyncEngine,
    connect: NodeFactory,
    make_merchant: MerchantFactory,
    page: Callable[[str], httpx.AsyncClient],
) -> None:
    slug = await make_merchant()
    client = await connect(None)
    params = {"date": DAY.isoformat(), "time": "20:00", "party_size": 2}

    async with page(slug) as http:
        paused = await http.post(f"/merchants/{slug}/agent/pause")
        with pytest.raises(AgentUnavailable, match="not taking bookings through agents"):
            await client.request("AVAILABILITY", slug, params)
        resumed = await http.post(f"/merchants/{slug}/agent/resume")
        slots = await client.request("AVAILABILITY", slug, params)

    assert (paused.status_code, paused.json()) == (200, {"status": "down"})
    assert (resumed.status_code, resumed.json()) == (200, {"status": "live"})
    assert slots["slots"]


async def test_a_building_agent_cant_be_resumed_from_the_agent_page(
    make_merchant: MerchantFactory, page: Callable[[str], httpx.AsyncClient]
) -> None:
    building = await make_merchant(live=False)

    async with page(building) as http:
        resumed = await http.post(f"/merchants/{building}/agent/resume")
        paused = await http.post(f"/merchants/{building}/agent/pause")

    assert (resumed.status_code, resumed.json()["error"]) == (422, "invalid_input")
    assert (paused.status_code, paused.json()["error"]) == (422, "invalid_input")
