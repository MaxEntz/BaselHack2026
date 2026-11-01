"""The BATP node on a real Postgres 16: every intent end to end through `BatpClient`.

Since BATP 0.3 (#254) a guest's agent books by talking to the merchant's agent: booking over
`TALK` is `test_node_talk.py`'s, and the booking rules are `AgentService`'s tests'.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date, datetime, time
from typing import cast
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.provisioning import set_status
from agent.service import PgAgentService
from agent.talk import MerchantAgent
from batp.client import BatpClient
from batp.codec import MAX_FRAME, encode
from batp.messages import PROTOCOL_VERSION, AgentIdentity, Hello
from batp.server import NodeServer
from core.errors import (
    InvalidInput,
)
from core.models import Reservation
from merchant_node.__main__ import NODE
from merchant_node.agents import MerchantAgents

pytestmark = pytest.mark.integration

MerchantFactory = Callable[..., Awaitable[str]]  # the `make_merchant` fixture

TZ = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 10, 31, 9, tzinfo=TZ)
DAY = date(2026, 11, 2)
GUEST = "Ada Lovelace"
GATEWAY = AgentIdentity(
    id="urn:batp:agent:claude-gateway",
    name="Claude via the Sésame gateway",
    kind="user-agent",
    operator="merchant-agents",
)


TALK_PARAMS = {"params": {"message": "Table for 2 on Monday?", "idempotency_key": "m-1"}}


def at(hhmm: str) -> str:
    return datetime.combine(DAY, time.fromisoformat(hhmm), tzinfo=TZ).isoformat()


@pytest.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[BatpClient]:
    service = PgAgentService(engine, tz=TZ, clock=lambda: NOW)

    def only_the_gateway(hello: Hello) -> bool:
        return hello.agent.id == GATEWAY.id and hello.token == "gw-token"

    node = NodeServer(
        node=NODE, handle=MerchantAgents(engine, service), authenticate=only_the_gateway
    )
    await node.start("127.0.0.1", 0)
    async with BatpClient("127.0.0.1", node.port, GATEWAY, token="gw-token") as batp:
        yield batp
    await node.stop()


async def test_ping_and_the_node_identity(client: BatpClient) -> None:
    result = await client.request("PING")

    assert result["pong"] is True
    assert client.welcome is not None
    assert client.welcome.node.id == "urn:batp:node:merchant-agents"


async def test_discover_finds_live_merchants_with_preferences(
    client: BatpClient, make_merchant: MerchantFactory
) -> None:
    token = f"zq{uuid4().hex[:6]}"
    live = await make_merchant(name=f"Zinc {token}")
    await make_merchant(name=f"Hidden {token}", live=False)

    result = await client.request(
        "DISCOVER",
        params={"query": token, "filters": {"tags": ["végétarien", "michelin"], "max_price": "€€"}},
    )

    assert [m["slug"] for m in result["merchants"]] == [live]
    assert result["ignored_filters"] == ["michelin"]


async def test_recall_finds_the_guest_s_bookings(
    client: BatpClient, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    booked = await book(engine, slug, GUEST)

    recalled = await client.request("RECALL", params={"guest_name": GUEST})

    assert booked.id in {r["id"] for r in recalled["reservations"]}


async def book(engine: AsyncEngine, slug: str, guest: str) -> Reservation:
    """A guest's booking, as the merchant's agent makes it on a confirm (#247)."""
    service = PgAgentService(engine, tz=TZ, clock=lambda: NOW)
    return await service.create_reservation(
        slug, datetime.fromisoformat(at("20:00")), 2, guest, str(uuid4()), via="claude"
    )


async def test_the_fallback_url_travels_on_the_wire(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    """The client raises `AgentError`s; the raw response still carries `fallback_url` (§8)."""
    slug = await make_merchant()
    await set_status(slug, "down", engine=engine)
    service = PgAgentService(engine, tz=TZ, clock=lambda: NOW)
    node = NodeServer(
        node=NODE, handle=MerchantAgents(engine, service), authenticate=lambda h: True
    )
    await node.start("127.0.0.1", 0)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", node.port, limit=MAX_FRAME)
        hello = {"type": "hello", "batp": PROTOCOL_VERSION, "agent": GATEWAY.model_dump()}
        writer.write(encode(hello))
        await reader.readline()
        writer.write(
            encode(
                {"type": "request", "id": "r-1", "intent": "TALK", "merchant": slug} | TALK_PARAMS
            )
        )
        response = json.loads(await asyncio.wait_for(reader.readline(), 5))
        writer.close()
    finally:
        await node.stop()

    assert response["status"] == 503
    assert response["error"]["code"] == "agent_unavailable"
    assert response["error"]["fallback_url"] == "http://localhost:8080/reservation.html"


async def test_discover_refuses_oversized_filters(client: BatpClient) -> None:
    with pytest.raises(InvalidInput, match="at most 32 tags"):
        await client.request("DISCOVER", params={"query": "x", "filters": {"tags": ["vegan"] * 33}})


async def test_a_notice_whose_recall_answer_was_lost_comes_back_on_the_next_recall(
    client: BatpClient, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    # The node handled the first RECALL, but its answer never reached the guest (#91).
    slug = await make_merchant()
    guest = f"Guest {uuid4().hex[:6]}"
    booked = await book(engine, slug, guest)
    merchant = PgAgentService(engine, tz=TZ, clock=lambda: NOW)
    await merchant.cancel_reservation(slug, booked.id, by="merchant")

    lost = await client.request("RECALL", params={"guest_name": guest})
    recalled = await client.request("RECALL", params={"guest_name": guest})

    assert len(lost["notices"]) == 1
    assert recalled["notices"] == lost["notices"]


async def test_a_handler_timeout_gives_the_merchants_fallback_url(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    """The node's own 503 (past its handler timeout) carries the link, as `down` does (#93)."""
    slug = await make_merchant()

    class SlowAgent:
        async def talk(self, *args: object) -> object:
            await asyncio.sleep(1)
            raise AssertionError("past the deadline: never answered")

    service = PgAgentService(engine, tz=TZ, clock=lambda: NOW)
    agents = MerchantAgents(engine, service, cast(MerchantAgent, SlowAgent()))
    node = NodeServer(
        node=NODE,
        handle=agents,
        authenticate=lambda h: True,
        intent_timeouts={"TALK": 0.2},
        fallback_url=agents.fallback_url,
    )
    await node.start("127.0.0.1", 0)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", node.port, limit=MAX_FRAME)
        hello = {"type": "hello", "batp": PROTOCOL_VERSION, "agent": GATEWAY.model_dump()}
        writer.write(encode(hello))
        await reader.readline()
        request = {"type": "request", "id": "r-1", "intent": "TALK", "merchant": slug}
        writer.write(encode(request | TALK_PARAMS))
        response = json.loads(await asyncio.wait_for(reader.readline(), 5))
        writer.close()
    finally:
        await node.stop()

    assert response["status"] == 503
    assert response["error"]["fallback_url"] == "http://localhost:8080/reservation.html"


# `TALK` (#245) on a node without the merchants' AI agent (no OPENAI_API_KEY); with it: #249,
# `test_node_talk.py`.

TALK = {"message": "Table for 4 tonight? Do you have vegetarian dishes?", "idempotency_key": "m-1"}


async def test_talk_to_a_merchant_not_published_is_404(
    client: BatpClient, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(live=False)

    _, response = await client.exchange("TALK", slug, TALK)

    assert (response.status, response.error and response.error.code) == (404, "not_found")


async def test_talk_to_a_down_merchant_is_503_with_its_fallback_url(
    engine: AsyncEngine, client: BatpClient, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    await set_status(slug, "down", engine=engine)

    _, response = await client.exchange("TALK", slug, TALK)

    assert response.status == 503 and response.error is not None
    assert response.error.code == "agent_unavailable"
    assert response.error.fallback_url == "http://localhost:8080/reservation.html"


async def test_talk_without_an_agent_gives_the_merchants_fallback_url(
    client: BatpClient, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()

    _, response = await client.exchange("TALK", slug, TALK)

    assert client.welcome is not None and "TALK" in client.welcome.intents
    assert response.status == 503 and response.error is not None
    assert response.error.code == "agent_unavailable"
    assert "can't talk right now" in response.error.message
    assert response.error.fallback_url == "http://localhost:8080/reservation.html"


async def test_talk_with_invalid_params_is_400(
    client: BatpClient, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()

    with pytest.raises(InvalidInput, match="message"):
        await client.request("TALK", slug, {**TALK, "message": ""})
    with pytest.raises(InvalidInput, match="Invalid params for TALK"):
        await client.request("TALK", slug, {**TALK, "confirm": "p-1"})  # no conversation
