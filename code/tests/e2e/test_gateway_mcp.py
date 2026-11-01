"""AGENTS.md §9 user scenarios, through a real MCP client over Streamable HTTP (#14).

Only the merchant's agent books, moves and cancels (#254): those scenarios run on
`talking_gateway`, bridged to a real node whose agent runs on a scripted model. What the
gateway decides on its own (its tools, the search, the guest's bookings, the refusals before
any agent) runs in both of `gateway_base_url`'s modes.
"""

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

from agent.discovery import find_merchant
from agent.provisioning import set_status
from agent.service import PgAgentService
from batp.client import BatpClient, parse_batp_url
from core.config import Settings
from core.db import merchant_conn, reservations_t
from core.models import ToolError
from gateway.agent_client import GATEWAY_AGENT, BatpAgentClient
from gateway.fallback import TALK_TOOL, call_agent
from gateway.server import create_server
from tests.e2e.conftest import TalkingNode, book, call, call_at, offered, talk
from tests.integration.agent_fakes import TOKEN, ScriptedLLM, calls, says
from tests.integration.agent_fakes import call as tool_call

pytestmark = pytest.mark.integration

ZINC = "le-petit-zinc"
ZINC_FALLBACK = "http://localhost:8080/reservation.html"
TZ = ZoneInfo("Europe/Zurich")
TOOLS = {"search_merchants", "talk_to_merchant", "get_my_reservations"}


def day_ahead(days: int = 1) -> date:
    """A day that is always bookable: the service refuses past times, and the clock is real."""
    return datetime.now(TZ).date() + timedelta(days=days)


def at(day: date, hhmm: str) -> datetime:
    return datetime.combine(day, datetime.strptime(hhmm, "%H:%M").time(), TZ)


def guest() -> str:
    """A name of its own, since the database lives as long as the module."""
    return f"Alex {uuid4().hex[:8]}"


def service(engine: AsyncEngine) -> PgAgentService:
    """The service behind the agent and the platform: the gateway never exposes its writes."""
    return PgAgentService(engine, tz=TZ)


# What the gateway answers on its own, with or without a node


async def test_lists_the_three_tools_with_their_schemas(gateway_base_url: str) -> None:
    async with Client(f"{gateway_base_url}/mcp") as client:
        tools = (await client.list_tools()).tools

    assert {tool.name for tool in tools} == TOOLS
    assert all(tool.description and tool.input_schema and tool.annotations for tool in tools)


async def test_search_applies_the_preferences(gateway_base_url: str) -> None:
    found = await call(
        gateway_base_url,
        "search_merchants",
        query="table for 4 tonight",
        near="Mulhouse",
        filters={"tags": ["vegetarian"], "max_price": "€€"},
    )

    assert [merchant["slug"] for merchant in found["merchants"]] == ["la-graine-verte", ZINC]


async def test_search_with_no_match_suggests_which_filter_to_drop(gateway_base_url: str) -> None:
    found = await call(
        gateway_base_url, "search_merchants", query="dinner", filters={"tags": ["vegan", "terrace"]}
    )

    assert found["merchants"] == []
    assert found["suggestions"] == [
        {"drop": "vegan", "matches": 2},
        {"drop": "terrace", "matches": 1},
    ]


async def test_without_the_merchant_s_agent_a_talk_gets_the_fallback_url(
    gateway_base_url: str,
) -> None:
    # In process the gateway has no model; `python -m merchant_node` here has no key (#249).
    answer = await talk(f"{gateway_base_url}/mcp", "Table for 2 tonight?", merchant=ZINC)

    assert answer["error"] == "agent_unavailable"
    assert answer["fallback_url"] == ZINC_FALLBACK


async def test_outage_gives_the_fallback_url_and_restoring_needs_no_restart(
    gateway_base_url: str, e2e_engine: AsyncEngine
) -> None:
    search = {"query": "a table at Le Petit Zinc", "filters": {"tags": ["vegetarian"]}}
    await set_status(ZINC, "down", engine=e2e_engine)
    try:
        direct = await talk(f"{gateway_base_url}/mcp", "Table for 2?", merchant=ZINC)
        named = await call(gateway_base_url, "search_merchants", **search)
        generic = await call(gateway_base_url, "search_merchants", query="restaurant tonight")
    finally:
        await set_status(ZINC, "live", engine=e2e_engine)
    visible = await call(gateway_base_url, "search_merchants", **search)

    assert direct["error"] == "agent_unavailable"
    assert direct["fallback_url"] == ZINC_FALLBACK
    assert named["error"] == "agent_unavailable"
    assert named["fallback_url"] == ZINC_FALLBACK
    assert ZINC not in [merchant["slug"] for merchant in generic["merchants"]]
    assert [merchant["slug"] for merchant in visible["merchants"]] == [ZINC]


async def test_unknown_merchant_is_not_found(gateway_base_url: str) -> None:
    result = await talk(f"{gateway_base_url}/mcp", "Table for 2?", merchant="chez-personne")

    assert result["error"] == "not_found"
    assert result["fallback_url"] is None


async def test_merchant_cancellation_notice_comes_back_on_the_next_call(
    gateway_base_url: str, e2e_engine: AsyncEngine
) -> None:
    name = guest()
    booked = await service(e2e_engine).create_reservation(
        ZINC, at(day_ahead(2), "20:00"), 2, name, str(uuid4()), via="claude"
    )
    await service(e2e_engine).cancel_reservation(ZINC, booked.id, by="merchant")

    first = await call(gateway_base_url, "get_my_reservations", guest_name=name)
    second = await call(gateway_base_url, "get_my_reservations", guest_name=name)

    assert first["reservations"] == []
    assert len(first["notices"]) == 1
    assert booked.id in first["notices"][0]
    assert second["notices"] == first["notices"]


async def test_arguments_outside_the_schema_answer_invalid_input(gateway_base_url: str) -> None:
    result = await call_at(
        f"{gateway_base_url}/mcp",
        "talk_to_merchant",
        {"merchant": "Le Petit Zinc", "message": "Table?", "idempotency_key": "tonight"},
    )

    assert result["error"] == "invalid_input"
    assert "merchant" in result["message"]
    assert "idempotency_key" in result["message"]
    assert "Traceback" not in result["message"]


@pytest.mark.parametrize(
    "search",
    [
        {"query": "a table for four tonight, " * 21},  # 525 characters, as a guest may write
        {"query": "dinner", "filters": {"tags": [f"tag-{n}" for n in range(33)]}},
    ],
)
async def test_a_search_beyond_the_node_s_bounds_is_invalid_input_in_both_modes(
    gateway_base_url: str, search: dict[str, Any]
) -> None:
    # Over BATP, the node refuses it: a refusal, never an outage with a restaurant's link (#90).
    result = await call(gateway_base_url, "search_merchants", **search)

    assert result["error"] == "invalid_input"
    assert result["fallback_url"] is None


def a_talk(message: str, **arguments: str) -> dict[str, str]:
    return {"merchant": ZINC, "message": message, "idempotency_key": str(uuid4()), **arguments}


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("get_my_reservations", {"guest_name": "Ada\x00Lovelace"}),
        ("talk_to_merchant", a_talk("Table\x00?")),
        ("talk_to_merchant", a_talk("Hi", idempotency_key="00000000-0000-0000-0000-000000000000")),
        ("talk_to_merchant", a_talk("x" * 2001)),
        ("talk_to_merchant", a_talk("Hi", conversation="c-\x00")),
        ("talk_to_merchant", a_talk("Yes", confirm="p-1")),  # a confirm needs its conversation
    ],
    ids=["guest-nul", "message-nul", "nil-key", "long-message", "conversation-nul", "lone-confirm"],
)
async def test_inputs_the_contracts_refuse_are_invalid_input_not_an_outage(
    gateway_base_url: str, tool: str, arguments: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    # They used to reach Postgres or date arithmetic and come back as an outage (#79); none of
    # them reaches the merchant's agent either.
    with caplog.at_level(logging.ERROR, logger="gateway.tools"):
        result = await call_at(f"{gateway_base_url}/mcp", tool, arguments)

    assert result["error"] == "invalid_input"
    assert result["fallback_url"] is None
    assert "tool_failed" not in caplog.text


async def test_calls_are_logged_fast_and_without_guest_names(
    gateway_base_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    name = guest()
    with caplog.at_level(logging.INFO):
        await talk(f"{gateway_base_url}/mcp", f"Table for 2 under {name}?", merchant=ZINC)
        async with Client(f"{gateway_base_url}/mcp") as client:
            for _ in range(5):
                await client.call_tool("get_my_reservations", {"guest_name": name})

    assert name not in caplog.text
    lines = [record.__dict__ for record in caplog.records if record.getMessage() == "tool_call"]
    outcomes = {(line["tool"], line["outcome"]) for line in lines}
    assert outcomes == {("talk_to_merchant", "agent_unavailable"), ("get_my_reservations", "ok")}
    # AGENTS.md: < 300 ms server-side. The first call may open a connection.
    latencies = [line["latency_ms"] for line in lines if line["tool"] == "get_my_reservations"]
    assert max(latencies[1:]) < 300


async def test_seeded_bookings_go_through_the_real_service(
    gateway_base_url: str, e2e_engine: AsyncEngine
) -> None:
    day = day_ahead(5)
    now = datetime.combine(day, datetime.min.time(), TZ)

    assert await seed.seed_bookings(e2e_engine, now) == 0

    booked = await call(
        gateway_base_url, "get_my_reservations", guest_name=seed.TONIGHT[0].guest_name
    )
    assert [reservation["via"] for reservation in booked["reservations"]] == [seed.TONIGHT[0].via]


async def test_health_reports_the_database(gateway_base_url: str) -> None:
    async with httpx.AsyncClient() as http:
        response = await http.get(f"{gateway_base_url}/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


# What only the merchant's agent does: a real node, its agent on a scripted model


async def test_a_booking_is_stored_via_claude_and_a_resent_confirm_returns_it(
    talking_gateway: str, agent_llm: ScriptedLLM, e2e_engine: AsyncEngine
) -> None:
    url, name, starts_at = f"{talking_gateway}/mcp", guest(), at(day_ahead(), "19:00")
    agent_llm.then(
        calls(
            tool_call(
                "propose_booking", starts_at=starts_at.isoformat(), party_size=2, guest_name=name
            )
        ),
        says("A table for 2 tomorrow at 19:00. Shall I book it?"),
        says("Booked."),
    )
    proposed = await talk(url, f"Table for 2 tomorrow at 19:00, {name}.", merchant=ZINC)
    confirm = {
        "merchant": ZINC,
        "message": "Yes, book it.",
        "idempotency_key": str(uuid4()),
        "conversation": proposed["conversation"],
        "confirm": proposed["pending"]["id"],
    }
    first = await call_at(url, "talk_to_merchant", confirm)
    resent = await call_at(url, "talk_to_merchant", confirm)  # a retry: no model call, no write

    assert proposed["pending"]["action"] == "book"
    assert proposed["reservation"] is None
    assert first["reservation"]["id"].startswith("R-")
    assert first["tools_called"] == ["create_reservation"]
    assert resent == first
    async with merchant_conn(e2e_engine, ZINC) as conn:
        rows = list(
            await conn.execute(
                select(reservations_t.c.id, reservations_t.c.via).where(
                    reservations_t.c.guest_name == name
                )
            )
        )
    assert rows == [(first["reservation"]["id"], "claude")]


async def test_move_then_cancel_through_the_agent(
    talking_gateway: str, agent_llm: ScriptedLLM
) -> None:
    url, day, name = f"{talking_gateway}/mcp", day_ahead(), guest()
    booking = (await book(talking_gateway, agent_llm, ZINC, at(day, "20:00"), name))["id"]

    def proposes(tool: str, **arguments: Any) -> None:
        agent_llm.then(calls(tool_call(tool, reservation_id=booking, **arguments)), says("Sure?"))

    proposes("propose_change", guest_name=name, starts_at=at(day, "21:00").isoformat())
    agent_llm.then(says("Moved."))
    change = await talk(url, f"Move {booking} to 21:00, {name}.", merchant=ZINC)
    moved = await talk(
        url,
        "Yes.",
        merchant=ZINC,
        conversation=change["conversation"],
        confirm=change["pending"]["id"],
    )
    proposes("propose_cancellation", guest_name="Someone Else")
    wrong_name = await talk(url, f"Cancel {booking}, Someone Else.", merchant=ZINC)
    listed = await call(talking_gateway, "get_my_reservations", guest_name=name)
    proposes("propose_cancellation", guest_name=name)
    agent_llm.then(says("Cancelled."))
    cancel = await talk(url, f"Cancel {booking}, {name}.", merchant=ZINC)
    cancelled = await talk(
        url,
        "Yes.",
        merchant=ZINC,
        conversation=cancel["conversation"],
        confirm=cancel["pending"]["id"],
    )
    after = await call(talking_gateway, "get_my_reservations", guest_name=name)

    assert change["pending"]["action"] == "change"
    assert datetime.fromisoformat(moved["reservation"]["starts_at"]) == at(day, "21:00")
    assert wrong_name["pending"] is None  # the tool found no booking under that name
    assert [reservation["id"] for reservation in listed["reservations"]] == [booking]
    assert cancel["pending"]["action"] == "cancel"
    assert cancelled["reservation"]["status"] == "cancelled"
    assert after["reservations"] == []


async def test_a_party_too_large_gets_the_fallback_url_from_the_agent(
    talking_gateway: str, agent_llm: ScriptedLLM
) -> None:
    agent_llm.then(
        calls(tool_call("get_availability", date=day_ahead().isoformat(), party_size=8)),
        says("We seat up to 6: please book on our website."),
    )
    answer = await talk(f"{talking_gateway}/mcp", "A table for 8 tomorrow?", merchant=ZINC)

    assert answer["fallback_url"] == ZINC_FALLBACK
    assert answer["slots"] == [] and answer["pending"] is None


async def test_a_start_the_merchant_blocks_is_no_longer_offered(
    talking_gateway: str, agent_llm: ScriptedLLM, e2e_engine: AsyncEngine
) -> None:
    day = day_ahead(3)
    before = await offered(talking_gateway, agent_llm, ZINC, day, "21:00")
    await service(e2e_engine).block_slot(ZINC, at(day, "21:00"))
    try:
        after = await offered(talking_gateway, agent_llm, ZINC, day, "21:00")
    finally:
        await service(e2e_engine).unblock_slot(ZINC, at(day, "21:00"))

    assert "21:00" in before
    assert "21:00" not in after


async def test_a_slow_agent_gets_the_fallback_url_within_the_talk_s_budget(
    talking_node: TalkingNode, agent_llm: ScriptedLLM, e2e_engine: AsyncEngine
) -> None:
    """Chaos on one merchant only, and the connection goes back to the pool (#13's pitfall)."""
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused/db",
        agent_talk_timeout_ms=100,
        chaos_delay_ms=500,
        chaos_merchants=ZINC,
    )
    host, port = parse_batp_url(talking_node.url)
    agent = BatpAgentClient(BatpClient(host, port, GATEWAY_AGENT, TOKEN))
    agent_llm.then(says("Yes, we have tables tomorrow."))
    try:
        async with Client(create_server(agent, e2e_engine, settings)) as client:
            started = time.perf_counter()
            slow = await client.call_tool(
                "talk_to_merchant",
                {"merchant": ZINC, "message": "Tomorrow?", "idempotency_key": str(uuid4())},
            )
            elapsed = time.perf_counter() - started
            untouched = await client.call_tool(
                "talk_to_merchant",
                {
                    "merchant": "maison-albane",
                    "message": "Tomorrow?",
                    "idempotency_key": str(uuid4()),
                },
            )
    finally:
        await agent.close()

    assert slow.structured_content is not None and untouched.structured_content is not None
    assert slow.structured_content["result"]["error"] == "agent_unavailable"
    assert slow.structured_content["result"]["fallback_url"] == ZINC_FALLBACK
    assert elapsed < 0.45
    assert untouched.structured_content["result"]["reply"].startswith("Yes")


async def test_timeout_during_a_query_releases_its_connection(e2e_engine: AsyncEngine) -> None:
    """`wait_for` cancels mid-transaction: the rollback must still give the connection back."""
    settings = Settings(
        _env_file=None, database_url="postgresql+psycopg://unused/db", agent_talk_timeout_ms=100
    )
    zinc = await find_merchant(ZINC, engine=e2e_engine)

    async def stuck_in_transaction() -> None:
        async with merchant_conn(e2e_engine, ZINC) as conn:
            await conn.execute(select(func.pg_sleep(5)))

    started = time.perf_counter()
    result = await call_agent(zinc, TALK_TOOL, stuck_in_transaction, settings=settings)

    assert isinstance(result, ToolError)
    assert result.error == "agent_unavailable"
    assert time.perf_counter() - started < 1
    assert e2e_engine.pool.checkedout() == 0  # type: ignore[attr-defined]
    async with e2e_engine.connect() as conn:
        assert await conn.scalar(select(1)) == 1
