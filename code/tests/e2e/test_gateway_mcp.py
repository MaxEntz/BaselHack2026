"""AGENTS.md §9 user scenarios, through a real MCP client over Streamable HTTP (#14)."""

import logging
import time
from datetime import date, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
import seed
from mcp import Client
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.provisioning import set_status
from agent.service import PgAgentService
from core.config import Settings
from core.db import merchant_conn, reservations_t
from core.models import ToolError
from gateway.agent_client import LocalAgentClient
from gateway.discovery import find_merchant
from gateway.fallback import call_agent
from gateway.server import create_server

pytestmark = pytest.mark.integration

ZINC = "le-petit-zinc"
ZINC_FALLBACK = "http://localhost:8080/reservation.html"
TZ = ZoneInfo("Europe/Zurich")
TOOLS = {
    "search_merchants",
    "get_availability",
    "create_reservation",
    "update_reservation",
    "cancel_reservation",
    "get_my_reservations",
}


def day_ahead(days: int = 1) -> date:
    """A day that is always bookable: the service refuses past times, and the clock is real."""
    return datetime.now(TZ).date() + timedelta(days=days)


def at(day: date, hhmm: str) -> str:
    return datetime.combine(day, datetime.strptime(hhmm, "%H:%M").time(), TZ).isoformat()


def guest() -> str:
    """A name of its own, since the database lives as long as the module."""
    return f"Alex {uuid4().hex[:8]}"


async def call(client: Client, tool: str, **arguments: Any) -> dict[str, Any]:
    """The tool's result. Failures are results too, so that Claude can read them."""
    result = await client.call_tool(tool, arguments)
    assert not result.is_error
    assert result.structured_content is not None
    content: dict[str, Any] = result.structured_content["result"]
    return content


async def book(
    client: Client, day: date, hhmm: str = "20:00", party_size: int = 2
) -> dict[str, Any]:
    name = guest()
    booked = await call(
        client,
        "create_reservation",
        merchant=ZINC,
        starts_at=at(day, hhmm),
        party_size=party_size,
        guest_name=name,
        idempotency_key=str(uuid4()),
    )
    reservation: dict[str, Any] = booked["reservation"]
    return reservation


def merchant_side(engine: AsyncEngine) -> PgAgentService:
    """The service the platform assistant calls: the gateway never exposes these operations."""
    return PgAgentService(engine, tz=TZ)


async def test_lists_the_six_tools_with_their_schemas(gateway_base_url: str) -> None:
    async with Client(f"{gateway_base_url}/mcp") as client:
        tools = (await client.list_tools()).tools

    assert {tool.name for tool in tools} == TOOLS
    assert all(tool.description and tool.input_schema and tool.annotations for tool in tools)


async def test_search_applies_the_preferences(gateway_base_url: str) -> None:
    async with Client(f"{gateway_base_url}/mcp") as client:
        found = await call(
            client,
            "search_merchants",
            query="table for 4 tonight",
            near="Mulhouse",
            filters={"tags": ["vegetarian"], "max_price": "€€"},
        )

    assert [merchant["slug"] for merchant in found["merchants"]] == ["la-graine-verte", ZINC]


async def test_search_with_no_match_suggests_which_filter_to_drop(gateway_base_url: str) -> None:
    async with Client(f"{gateway_base_url}/mcp") as client:
        found = await call(
            client, "search_merchants", query="dinner", filters={"tags": ["vegan", "terrace"]}
        )

    assert found["merchants"] == []
    assert found["suggestions"] == [
        {"drop": "vegan", "matches": 2},
        {"drop": "terrace", "matches": 1},
    ]


async def test_availability_returns_at_most_3_sorted_slots(gateway_base_url: str) -> None:
    async with Client(f"{gateway_base_url}/mcp") as client:
        found = await call(
            client,
            "get_availability",
            merchant=ZINC,
            date=day_ahead().isoformat(),
            time="20:00",
            party_size=4,
        )

    starts = [slot["starts_at"] for slot in found["slots"]]
    assert 1 <= len(starts) <= 3
    assert starts == sorted(starts)


async def test_booking_is_stored_via_claude_and_replay_returns_it(
    gateway_base_url: str, e2e_engine: AsyncEngine
) -> None:
    arguments = {
        "merchant": ZINC,
        "starts_at": at(day_ahead(), "19:00"),
        "party_size": 2,
        "guest_name": guest(),
        "idempotency_key": str(uuid4()),
    }
    async with Client(f"{gateway_base_url}/mcp") as client:
        first = await call(client, "create_reservation", **arguments)
        replay = await call(client, "create_reservation", **arguments)

    assert first["reservation"]["id"].startswith("R-")
    assert replay == first
    async with merchant_conn(e2e_engine, ZINC) as conn:
        via = await conn.scalar(
            select(reservations_t.c.via).where(reservations_t.c.id == first["reservation"]["id"])
        )
    assert via == "claude"


async def test_move_then_cancel_a_booking(gateway_base_url: str) -> None:
    day = day_ahead()
    async with Client(f"{gateway_base_url}/mcp") as client:
        booked = await book(client, day)
        name, booking = booked["guest_name"], booked["id"]

        moved = await call(
            client,
            "update_reservation",
            merchant=ZINC,
            reservation_id=booking,
            guest_name=name,
            idempotency_key=str(uuid4()),
            starts_at=at(day, "21:00"),
        )
        wrong_name = await call(
            client,
            "cancel_reservation",
            merchant=ZINC,
            reservation_id=booking,
            guest_name="Someone Else",
        )
        listed = await call(client, "get_my_reservations", guest_name=name)
        cancelled = await call(
            client, "cancel_reservation", merchant=ZINC, reservation_id=booking, guest_name=name
        )
        after = await call(client, "get_my_reservations", guest_name=name)

    assert datetime.fromisoformat(moved["reservation"]["starts_at"]).astimezone(TZ).hour == 21
    assert wrong_name["error"] == "not_found"
    assert wrong_name["fallback_url"] is None
    assert [reservation["id"] for reservation in listed["reservations"]] == [booking]
    assert cancelled == {"status": "cancelled"}
    assert after["reservations"] == []


async def test_update_without_change_is_invalid_input(gateway_base_url: str) -> None:
    async with Client(f"{gateway_base_url}/mcp") as client:
        booked = await book(client, day_ahead())
        result = await call(
            client,
            "update_reservation",
            merchant=ZINC,
            reservation_id=booked["id"],
            guest_name=booked["guest_name"],
            idempotency_key=str(uuid4()),
        )

    assert result["error"] == "invalid_input"


async def test_party_too_large_gives_the_fallback_url(gateway_base_url: str) -> None:
    async with Client(f"{gateway_base_url}/mcp") as client:
        result = await call(
            client, "get_availability", merchant=ZINC, date=day_ahead().isoformat(), party_size=8
        )

    assert result["error"] == "party_too_large"
    assert result["fallback_url"] == ZINC_FALLBACK
    assert ZINC_FALLBACK in result["message"]


async def test_outage_gives_the_fallback_url_and_restoring_needs_no_restart(
    gateway_base_url: str, e2e_engine: AsyncEngine
) -> None:
    search = {"query": "a table at Le Petit Zinc", "filters": {"tags": ["vegetarian"]}}
    availability = {"merchant": ZINC, "date": day_ahead().isoformat(), "party_size": 2}
    async with Client(f"{gateway_base_url}/mcp") as client:
        await set_status(ZINC, "down", engine=e2e_engine)
        try:
            direct = await call(client, "get_availability", **availability)
            named = await call(client, "search_merchants", **search)
            generic = await call(client, "search_merchants", query="restaurant tonight")
        finally:
            await set_status(ZINC, "live", engine=e2e_engine)
        restored = await call(client, "get_availability", **availability)
        visible = await call(client, "search_merchants", **search)

    assert direct["error"] == "agent_unavailable"
    assert direct["fallback_url"] == ZINC_FALLBACK
    assert named["error"] == "agent_unavailable"
    assert named["fallback_url"] == ZINC_FALLBACK
    assert ZINC not in [merchant["slug"] for merchant in generic["merchants"]]
    assert restored["slots"]
    assert [merchant["slug"] for merchant in visible["merchants"]] == [ZINC]


async def test_unknown_merchant_is_not_found(gateway_base_url: str) -> None:
    async with Client(f"{gateway_base_url}/mcp") as client:
        result = await call(
            client,
            "get_availability",
            merchant="chez-personne",
            date=day_ahead().isoformat(),
            party_size=2,
        )

    assert result["error"] == "not_found"
    assert result["fallback_url"] is None


async def test_merchant_cancellation_notice_appears_once(
    gateway_base_url: str, e2e_engine: AsyncEngine
) -> None:
    async with Client(f"{gateway_base_url}/mcp") as client:
        booked = await book(client, day_ahead(2))
        await merchant_side(e2e_engine).cancel_reservation(ZINC, booked["id"], by="merchant")

        first = await call(client, "get_my_reservations", guest_name=booked["guest_name"])
        second = await call(client, "get_my_reservations", guest_name=booked["guest_name"])

    assert first["reservations"] == []
    assert len(first["notices"]) == 1
    assert booked["id"] in first["notices"][0]
    assert second["notices"] == []


async def test_blocked_start_is_no_longer_offered(
    gateway_base_url: str, e2e_engine: AsyncEngine
) -> None:
    day = day_ahead(3)
    blocked = datetime.fromisoformat(at(day, "21:00"))
    availability = {
        "merchant": ZINC,
        "date": day.isoformat(),
        "time": "21:00",
        "party_size": 2,
    }
    async with Client(f"{gateway_base_url}/mcp") as client:
        before = await call(client, "get_availability", **availability)
        await merchant_side(e2e_engine).block_slot(ZINC, blocked)
        try:
            after = await call(client, "get_availability", **availability)
        finally:
            await merchant_side(e2e_engine).unblock_slot(ZINC, blocked)

    def starts(result: dict[str, Any]) -> set[datetime]:
        return {datetime.fromisoformat(slot["starts_at"]) for slot in result["slots"]}

    assert blocked in starts(before)
    assert blocked not in starts(after)


async def test_arguments_outside_the_schema_answer_invalid_input(gateway_base_url: str) -> None:
    async with Client(f"{gateway_base_url}/mcp") as client:
        result = await call(
            client, "get_availability", merchant="Le Petit Zinc", date="tonight", party_size=0
        )

    assert result["error"] == "invalid_input"
    assert "merchant" in result["message"]
    assert "Traceback" not in result["message"]


async def test_calls_are_logged_fast_and_without_guest_names(
    gateway_base_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        async with Client(f"{gateway_base_url}/mcp") as client:
            booked = await book(client, day_ahead(4))
            for _ in range(5):
                await call(
                    client,
                    "get_availability",
                    merchant=ZINC,
                    date=day_ahead(4).isoformat(),
                    party_size=2,
                )
            await call(client, "get_my_reservations", guest_name=booked["guest_name"])

    assert booked["guest_name"] not in caplog.text
    calls = [record.__dict__ for record in caplog.records if record.getMessage() == "tool_call"]
    assert {call["tool"] for call in calls} >= {"create_reservation", "get_availability"}
    assert all(call["outcome"] == "ok" for call in calls)
    # AGENTS.md: < 300 ms server-side. The first call may open a connection.
    latencies = [call["latency_ms"] for call in calls if call["tool"] == "get_availability"]
    assert max(latencies[1:]) < 300


async def test_seeded_bookings_go_through_the_real_service(
    gateway_base_url: str, e2e_engine: AsyncEngine
) -> None:
    day = day_ahead(5)
    now = datetime.combine(day, datetime.min.time(), TZ)

    assert await seed.seed_bookings(e2e_engine, now) == 0

    async with Client(f"{gateway_base_url}/mcp") as client:
        booked = await call(client, "get_my_reservations", guest_name=seed.TONIGHT[0].guest_name)
    assert [reservation["via"] for reservation in booked["reservations"]] == [seed.TONIGHT[0].via]


async def test_health_reports_the_database(gateway_base_url: str) -> None:
    async with httpx.AsyncClient() as http:
        response = await http.get(f"{gateway_base_url}/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


async def test_slow_agent_gets_the_fallback_url_within_the_timeout(
    e2e_database_url: str, e2e_engine: AsyncEngine
) -> None:
    """Chaos on one merchant only, and the connection goes back to the pool (#13's pitfall)."""
    settings = Settings(
        _env_file=None,
        database_url=e2e_database_url,
        agent_timeout_ms=100,
        chaos_delay_ms=500,
        chaos_merchants=ZINC,
    )
    agent = LocalAgentClient(PgAgentService(e2e_engine, tz=TZ), e2e_engine)
    availability = {"date": day_ahead().isoformat(), "party_size": 2}
    async with Client(create_server(agent, e2e_engine, settings)) as client:
        started = time.perf_counter()
        slow = await call(client, "get_availability", merchant=ZINC, **availability)
        elapsed = time.perf_counter() - started
        untouched = await call(client, "get_availability", merchant="maison-albane", **availability)

    assert slow["error"] == "agent_unavailable"
    assert slow["fallback_url"] == ZINC_FALLBACK
    assert elapsed < 0.45
    assert "slots" in untouched


async def test_timeout_during_a_query_releases_its_connection(e2e_engine: AsyncEngine) -> None:
    """`wait_for` cancels mid-transaction: the rollback must still give the connection back."""
    settings = Settings(
        _env_file=None, database_url="postgresql+psycopg://unused/db", agent_timeout_ms=100
    )
    zinc = await find_merchant(ZINC, engine=e2e_engine)

    async def stuck_in_transaction() -> None:
        async with merchant_conn(e2e_engine, ZINC) as conn:
            await conn.execute(select(func.pg_sleep(5)))

    started = time.perf_counter()
    result = await call_agent(zinc, "create_reservation", stuck_in_transaction, settings=settings)

    assert isinstance(result, ToolError)
    assert result.error == "agent_unavailable"
    assert time.perf_counter() - started < 1
    assert e2e_engine.pool.checkedout() == 0  # type: ignore[attr-defined]
    async with e2e_engine.connect() as conn:
        assert await conn.scalar(select(1)) == 1
