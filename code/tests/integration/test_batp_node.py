"""The BATP node on a real Postgres 16: every intent end to end through `BatpClient`."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date, datetime, time
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.provisioning import set_status
from agent.service import PgAgentService
from batp.client import BatpClient
from batp.codec import MAX_FRAME, encode
from batp.messages import AgentIdentity, Hello
from batp.server import NodeServer
from core.errors import (
    AgentUnavailable,
    InvalidInput,
    NotFound,
    PartyTooLarge,
    SlotUnavailable,
)
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
    name="Claude via the merchant-agents gateway",
    kind="user-agent",
    operator="merchant-agents",
)


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


async def test_book_amend_cancel_and_recall(
    client: BatpClient, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()

    slots = await client.request(
        "AVAILABILITY", slug, {"date": DAY.isoformat(), "time": "20:00", "party_size": 4}
    )
    assert slots["slots"]
    assert len(slots["slots"]) <= 3

    key = str(uuid4())
    booking = {
        "starts_at": at("20:00"),
        "party_size": 4,
        "guest_name": GUEST,
        "idempotency_key": key,
    }
    booked = (await client.request("BOOK", slug, booking))["reservation"]
    replayed = (await client.request("BOOK", slug, booking))["reservation"]
    assert replayed["id"] == booked["id"]
    assert (booked["merchant"], booked["via"], booked["status"]) == (slug, "claude", "confirmed")

    moved = await client.request(
        "AMEND",
        slug,
        {
            "reservation_id": booked["id"],
            "guest_name": GUEST,
            "starts_at": at("21:00"),
            "idempotency_key": str(uuid4()),
        },
    )
    assert moved["reservation"]["starts_at"] == at("21:00")

    recalled = await client.request("RECALL", params={"guest_name": GUEST})
    assert booked["id"] in {r["id"] for r in recalled["reservations"]}

    cancelled = await client.request(
        "CANCEL", slug, {"reservation_id": booked["id"], "guest_name": GUEST}
    )
    assert cancelled["reservation"]["status"] == "cancelled"


async def test_refusals_carry_their_status_and_fallback_url(
    client: BatpClient, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(tables=[{"label": "T1", "seats": 4}], seats=4)
    booking = {"starts_at": at("20:00"), "party_size": 4, "guest_name": GUEST}

    with pytest.raises(PartyTooLarge):
        await client.request("AVAILABILITY", slug, {"date": DAY.isoformat(), "party_size": 12})
    await client.request("BOOK", slug, {**booking, "idempotency_key": str(uuid4())})
    with pytest.raises(SlotUnavailable):
        await client.request("BOOK", slug, {**booking, "idempotency_key": str(uuid4())})
    with pytest.raises(NotFound):
        await client.request(
            "AVAILABILITY", "no-such-merchant", {"date": DAY.isoformat(), "party_size": 2}
        )
    with pytest.raises(InvalidInput, match="party_size"):
        await client.request("AVAILABILITY", slug, {"date": DAY.isoformat(), "party_size": "many"})
    with pytest.raises(InvalidInput, match="needs a merchant"):
        await client.request("AVAILABILITY", params={"date": DAY.isoformat(), "party_size": 2})


async def test_a_down_merchant_refuses_agents_with_its_fallback_url(
    engine: AsyncEngine, client: BatpClient, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    await set_status(slug, "down", engine=engine)

    with pytest.raises(AgentUnavailable, match="not taking bookings through agents"):
        await client.request("AVAILABILITY", slug, {"date": DAY.isoformat(), "party_size": 2})


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
        writer.write(encode({"type": "hello", "batp": "0.1", "agent": GATEWAY.model_dump()}))
        await reader.readline()
        writer.write(
            encode(
                {
                    "type": "request",
                    "id": "r-1",
                    "intent": "AVAILABILITY",
                    "merchant": slug,
                    "params": {"date": DAY.isoformat(), "party_size": 2},
                }
            )
        )
        response = json.loads(await asyncio.wait_for(reader.readline(), 5))
        writer.close()
    finally:
        await node.stop()

    assert response["status"] == 503
    assert response["error"]["code"] == "agent_unavailable"
    assert response["error"]["fallback_url"] == "http://localhost:8080/reservation.html"


async def test_a_declared_kind_does_not_change_who_booked(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    """A caller that claims to be a merchant agent still books as a guest's agent (`claude`)."""
    slug = await make_merchant()
    service = PgAgentService(engine, tz=TZ, clock=lambda: NOW)
    node = NodeServer(
        node=NODE, handle=MerchantAgents(engine, service), authenticate=lambda h: True
    )
    await node.start("127.0.0.1", 0)
    impostor = GATEWAY.model_copy(update={"kind": "merchant-agent"})
    try:
        async with BatpClient("127.0.0.1", node.port, impostor) as batp:
            booked = await batp.request(
                "BOOK",
                slug,
                {
                    "starts_at": at("20:00"),
                    "party_size": 2,
                    "guest_name": GUEST,
                    "idempotency_key": str(uuid4()),
                },
            )
    finally:
        await node.stop()

    assert booked["reservation"]["via"] == "claude"


async def test_discover_refuses_oversized_filters(client: BatpClient) -> None:
    with pytest.raises(InvalidInput, match="at most 32 tags"):
        await client.request("DISCOVER", params={"query": "x", "filters": {"tags": ["vegan"] * 33}})


async def test_a_notice_whose_recall_answer_was_lost_comes_back_on_the_next_recall(
    client: BatpClient, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    # The node handled the first RECALL, but its answer never reached the guest (#91).
    slug = await make_merchant()
    guest = f"Guest {uuid4().hex[:6]}"
    booking = {
        "starts_at": at("20:00"),
        "party_size": 2,
        "guest_name": guest,
        "idempotency_key": str(uuid4()),
    }
    booked = (await client.request("BOOK", slug, booking))["reservation"]
    merchant = PgAgentService(engine, tz=TZ, clock=lambda: NOW)
    await merchant.cancel_reservation(slug, booked["id"], by="merchant")

    lost = await client.request("RECALL", params={"guest_name": guest})
    recalled = await client.request("RECALL", params={"guest_name": guest})

    assert len(lost["notices"]) == 1
    assert recalled["notices"] == lost["notices"]


async def test_a_handler_timeout_gives_the_merchants_fallback_url(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    """The node's own 503 (past its handler timeout) carries the link, as `down` does (#93)."""
    slug = await make_merchant()

    class SlowService(PgAgentService):
        async def get_availability(self, *args: object, **kwargs: object) -> list[object]:
            await asyncio.sleep(1)
            return []

    agents = MerchantAgents(engine, SlowService(engine, tz=TZ, clock=lambda: NOW))
    node = NodeServer(
        node=NODE,
        handle=agents,
        authenticate=lambda h: True,
        handler_timeout=0.2,
        fallback_url=agents.fallback_url,
    )
    await node.start("127.0.0.1", 0)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", node.port, limit=MAX_FRAME)
        writer.write(encode({"type": "hello", "batp": "0.1", "agent": GATEWAY.model_dump()}))
        await reader.readline()
        request = {"type": "request", "id": "r-1", "intent": "AVAILABILITY", "merchant": slug}
        writer.write(encode(request | {"params": {"date": DAY.isoformat(), "party_size": 2}}))
        response = json.loads(await asyncio.wait_for(reader.readline(), 5))
        writer.close()
    finally:
        await node.stop()

    assert response["status"] == 503
    assert response["error"]["fallback_url"] == "http://localhost:8080/reservation.html"
