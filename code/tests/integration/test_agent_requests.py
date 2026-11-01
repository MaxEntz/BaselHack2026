"""The node's request log and the pause switch (#146), on a real Postgres 16.

Since BATP 0.3 (#254) a merchant is addressed only by `TALK`: the merchant's agent runs on a
scripted model here, so each test says what the model answers.
"""

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
from agent.talk import MerchantAgent, registry_merchants
from batp.client import BatpClient
from batp.codec import encode
from batp.messages import AgentIdentity, Hello, Request, Response
from batp.server import NodeServer, Recorder
from core.config import Settings
from core.db import agent_requests_t
from core.errors import AgentUnavailable, InvalidInput, NotFound
from core.models import AgentRequests
from merchant_node.__main__ import NODE
from merchant_node.agents import MerchantAgents
from merchant_node.requests_log import RequestLog, log_engine
from merchant_platform.api import create_app
from tests.integration.agent_fakes import ScriptedLLM, call, calls, says

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


def talk(message: str, **more: str) -> dict[str, str]:
    """`TALK` params: a new key for each message."""
    return {"message": message, "idempotency_key": str(uuid4()), **more}


@pytest.fixture
def service(engine: AsyncEngine) -> PgAgentService:
    return PgAgentService(engine, tz=TZ, clock=lambda: NOW)


@pytest.fixture
def llm() -> ScriptedLLM:
    """The merchant agent's model: each test queues what it answers (`llm.then`)."""
    return ScriptedLLM()


@pytest.fixture
async def connect(
    engine: AsyncEngine, service: PgAgentService, llm: ScriptedLLM
) -> AsyncIterator[NodeFactory]:
    """A node with the given recorder, and a client signed in as the gateway."""
    nodes: list[NodeServer] = []
    clients: list[BatpClient] = []

    def only_the_gateway(hello: Hello) -> bool:
        return hello.agent.id == GATEWAY.id and hello.token == TOKEN

    async def start(record: Recorder | None) -> BatpClient:
        talker = MerchantAgent(llm, service, registry_merchants(engine), TZ, clock=lambda: NOW)
        agents = MerchantAgents(engine, service, talker)
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


async def test_a_turn_is_logged_with_both_frames_and_never_the_token(
    engine: AsyncEngine, connect: NodeFactory, llm: ScriptedLLM, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    client = await connect(RequestLog(engine))
    params = talk("A table for 2 on Monday?")
    llm.then(says("Which time?"))

    answered = await client.request("TALK", slug, params)

    [row] = await settled(engine, slug, 1)
    assert (row["agent_name"], row["intent"], row["status"]) == (GATEWAY.name, "TALK", 200)
    assert row["request"]["intent"] == "TALK"
    assert row["request"]["merchant"] == slug
    assert row["request"]["params"] == params
    assert row["response"]["status"] == 200
    assert row["response"]["result"] == answered
    stored = row["request_text"] + row["response_text"]
    assert TOKEN not in stored
    assert "hello" not in stored
    # The text that was on the wire, never re-serialized by Postgres (#129).
    response = Response(id=row["response"]["id"], status=200, result=answered)
    assert row["response_text"] == encode(response).decode().removesuffix("\n")
    assert row["request_text"].startswith('{"type":"request","id":')


async def test_a_refusal_is_logged_with_its_fallback_url(
    engine: AsyncEngine, connect: NodeFactory, llm: ScriptedLLM, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(tables=[{"label": "T1", "seats": 4}], seats=4)
    client = await connect(RequestLog(engine))
    llm.then(
        calls(call("get_availability", date=DAY.isoformat(), party_size=12)),
        says("We can't seat 12 online."),
    )

    # A tool's refusal goes to the model: the agent answered, and code sets the link.
    answered = await client.request("TALK", slug, talk("A table for 12 on Monday?"))

    [row] = await settled(engine, slug, 1)
    assert answered["fallback_url"]
    assert row["status"] == 200
    assert row["response"]["result"]["fallback_url"] == answered["fallback_url"]


async def test_requests_without_a_published_merchant_leave_no_row(
    engine: AsyncEngine, connect: NodeFactory, llm: ScriptedLLM, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    building = await make_merchant(live=False)
    client = await connect(RequestLog(engine))

    await client.request("PING")
    await client.request("DISCOVER", params={"query": "zinc"})
    await client.request("RECALL", params={"guest_name": GUEST})
    with pytest.raises(NotFound):
        await client.request("TALK", "chez-personne", talk("A table?"))
    with pytest.raises(NotFound):
        await client.request("TALK", building, talk("A table?"))
    llm.then(says("Which day?"))
    await client.request("TALK", slug, talk("A table?"))

    found = await settled(engine, slug, 1)
    async with engine.connect() as conn:
        unknown = await conn.scalar(
            select(func.count())
            .select_from(agent_requests_t)
            .where(agent_requests_t.c.merchant_slug == "chez-personne")
        )
    assert [row["intent"] for row in found] == ["TALK"]
    assert unknown == 0
    assert await rows(engine, building) == []


async def test_rows_older_than_7_days_are_purged_as_new_ones_arrive(
    engine: AsyncEngine, connect: NodeFactory, llm: ScriptedLLM, make_merchant: MerchantFactory
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
    llm.then(says("Which day?"))

    await client.request("TALK", slug, talk("A table?"))

    for _ in range(100):
        found = await rows(engine, slug)
        if [row["agent_name"] for row in found] == [GATEWAY.name]:
            break
        await asyncio.sleep(0.02)
    assert [row["agent_name"] for row in found] == [GATEWAY.name]


async def test_a_failing_or_slow_log_never_delays_or_fails_the_answer(
    connect: NodeFactory, llm: ScriptedLLM, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    calls: list[str] = []

    async def broken(agent: AgentIdentity, request: Request, response: Response) -> None:
        calls.append(request.intent)
        raise RuntimeError("the log is down")

    async def slow(agent: AgentIdentity, request: Request, response: Response) -> None:
        await asyncio.sleep(3)

    llm.then(says("Which day?"), says("Which day?"), says("Which day?"))
    failing = await connect(broken)
    assert (await failing.request("TALK", slug, talk("A table?")))["reply"]
    assert (await failing.request("TALK", slug, talk("A table?")))["reply"]

    lagging = await connect(slow)
    started = clock.monotonic()
    assert (await lagging.request("TALK", slug, talk("A table?")))["reply"]
    assert clock.monotonic() - started < 1

    await asyncio.sleep(0.05)
    assert calls == ["TALK", "TALK"]


async def test_the_log_has_its_own_small_pool_and_drops_records_when_full(
    engine: AsyncEngine,
    connect: NodeFactory,
    llm: ScriptedLLM,
    make_merchant: MerchantFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    slug = await make_merchant()
    own = log_engine(engine.url.render_as_string(hide_password=False))
    try:
        assert (own.pool.size(), own.pool._max_overflow) == (1, 1)  # type: ignore[attr-defined]
        client = await connect(RequestLog(own, max_pending=0))

        llm.then(says("Which day?"))
        answered = await client.request("TALK", slug, talk("A table?"))
        await asyncio.sleep(0.2)
    finally:
        await own.dispose()

    assert answered["reply"] == "Which day?"
    assert await rows(engine, slug) == []
    assert "request log full: record dropped" in caplog.text


async def test_pause_sends_agents_to_the_booking_page_and_resume_brings_slots_back(
    engine: AsyncEngine,
    service: PgAgentService,
    connect: NodeFactory,
    llm: ScriptedLLM,
    make_merchant: MerchantFactory,
) -> None:
    slug = await make_merchant()
    client = await connect(RequestLog(engine))
    asked = {"date": DAY.isoformat(), "time": "20:00", "party_size": 2}

    await service.set_status(slug, "down")
    with pytest.raises(AgentUnavailable):
        await client.request("TALK", slug, talk("A table for 2 on Monday at 8?"))
    await service.set_status(slug, "live")
    llm.then(calls(call("get_availability", **asked)), says("20:00 is free."))
    resumed = await client.request("TALK", slug, talk("A table for 2 on Monday at 8?"))

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
    llm: ScriptedLLM,
    make_merchant: MerchantFactory,
    page: Callable[[str], httpx.AsyncClient],
) -> None:
    slug = await make_merchant(max_party_size=6)
    client = await connect(RequestLog(engine))
    llm.then(
        calls(call("propose_booking", starts_at=at("20:00"), party_size=2, guest_name=GUEST)),
        says("A table for 2 on Monday at 20:00. Shall I book it?"),
        says("Booked."),
    )
    proposed = await client.request("TALK", slug, talk("Monday 20:00 for 2, Ada Lovelace"))
    conversation, proposal = proposed["conversation"], proposed["pending"]["id"]
    await settled(engine, slug, 1)
    booked = await client.request(
        "TALK", slug, talk("Yes", conversation=conversation, confirm=proposal)
    )
    await settled(engine, slug, 2)
    with pytest.raises(NotFound):
        await client.request("TALK", slug, talk("Hello again", conversation="c-ended"))
    stored = await settled(engine, slug, 3)
    day = await local_day(engine, slug)

    async with page(slug) as http:
        everything = await http.get(f"/merchants/{slug}/agent/requests?date={day}")
        refused = await http.get(f"/merchants/{slug}/agent/requests?date={day}&refused=true")
        other_day = await http.get(
            f"/merchants/{slug}/agent/requests?date={day - timedelta(days=1)}"
        )

    assert everything.status_code == 200
    assert booked["reservation"]["status"] == "confirmed"
    listed = AgentRequests.model_validate(everything.json()).requests
    assert [(r.intent, r.status, r.refused) for r in listed] == [
        ("TALK", 404, True),
        ("TALK", 200, False),
        ("TALK", 200, False),
    ]
    ended, done, waiting = listed
    assert ended.title == f"{GATEWAY.name} wrote in a conversation that had ended"
    assert done.title == f"{GUEST} booked through {GATEWAY.name}"
    assert done.text.startswith("Mon 2 Nov, 20:00, 2 guests, table ")
    assert waiting.title == f"{GATEWAY.name} is waiting for its guest's yes"
    assert done.agent_name == GATEWAY.name
    assert done.at.utcoffset() == TZ.utcoffset(done.at.replace(tzinfo=None))
    # The frames as stored: the wire's text, never re-serialized.
    assert (done.request, done.response) == (
        stored[1]["request_text"],
        stored[1]["response_text"],
    )
    assert TOKEN not in everything.text
    assert [r.status for r in AgentRequests.model_validate(refused.json()).requests] == [404]
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
    llm: ScriptedLLM,
    make_merchant: MerchantFactory,
    page: Callable[[str], httpx.AsyncClient],
) -> None:
    slug = await make_merchant()
    client = await connect(None)
    asked = {"date": DAY.isoformat(), "time": "20:00", "party_size": 2}

    async with page(slug) as http:
        paused = await http.post(f"/merchants/{slug}/agent/pause")
        with pytest.raises(AgentUnavailable, match="not taking bookings through agents"):
            await client.request("TALK", slug, talk("A table for 2 on Monday at 8?"))
        resumed = await http.post(f"/merchants/{slug}/agent/resume")
        llm.then(calls(call("get_availability", **asked)), says("20:00 is free."))
        answered = await client.request("TALK", slug, talk("A table for 2 on Monday at 8?"))

    assert (paused.status_code, paused.json()) == (200, {"status": "down"})
    assert (resumed.status_code, resumed.json()) == (200, {"status": "live"})
    assert answered["slots"]


async def test_a_building_agent_cant_be_resumed_from_the_agent_page(
    make_merchant: MerchantFactory, page: Callable[[str], httpx.AsyncClient]
) -> None:
    building = await make_merchant(live=False)

    async with page(building) as http:
        resumed = await http.post(f"/merchants/{building}/agent/resume")
        paused = await http.post(f"/merchants/{building}/agent/pause")

    assert (resumed.status_code, resumed.json()["error"]) == (422, "invalid_input")
    assert (paused.status_code, paused.json()["error"]) == (422, "invalid_input")
