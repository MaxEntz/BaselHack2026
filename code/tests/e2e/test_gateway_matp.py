"""What only exists with the bridge: MCP → MATP → node, and that node dying or freezing (#61)."""

import json
import logging
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from mcp import Client

if TYPE_CHECKING:
    from tests.e2e.conftest import MatpNode

pytestmark = pytest.mark.integration

ZINC = "le-petit-zinc"
ZINC_FALLBACK = "http://localhost:8080/reservation.html"
TZ = ZoneInfo("Europe/Zurich")
GATEWAY_AGENT = "urn:matp:agent:claude-gateway"


def availability() -> dict[str, Any]:
    day = datetime.now(TZ).date() + timedelta(days=1)
    return {"merchant": ZINC, "date": day.isoformat(), "time": "20:00", "party_size": 2}


async def call(client: Client, tool: str, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(tool, arguments)
    assert not result.is_error
    assert result.structured_content is not None
    content: dict[str, Any] = result.structured_content["result"]
    return content


async def test_a_booking_crosses_the_node_under_the_gateway_s_identity(
    bridge_base_url: str, matp_node: "MatpNode", caplog: pytest.LogCaptureFixture
) -> None:
    guest = f"Alex {uuid4().hex[:8]}"
    day = datetime.now(TZ).date() + timedelta(days=1)
    starts_at = datetime.combine(day, datetime.strptime("19:30", "%H:%M").time(), TZ)

    with caplog.at_level(logging.INFO, logger="gateway.tools"):
        async with Client(f"{bridge_base_url}/mcp") as client:
            booked = await call(
                client,
                "create_reservation",
                merchant=ZINC,
                starts_at=starts_at.isoformat(),
                party_size=2,
                guest_name=guest,
                idempotency_key=str(uuid4()),
            )

    assert booked["reservation"]["via"] == "claude"
    gateway_line = next(r.__dict__ for r in caplog.records if r.getMessage() == "tool_call")
    assert gateway_line["outcome"] == "ok"
    node_lines = [json.loads(line) for line in matp_node.logs().splitlines() if '"BOOK"' in line]
    assert any(
        line.get("agent") == GATEWAY_AGENT and line.get("id") == gateway_line["matp_request"]
        for line in node_lines
    ), matp_node.logs()
    assert guest not in matp_node.logs()
    assert guest not in caplog.text


async def test_every_tool_goes_through_the_node(
    bridge_base_url: str, matp_node: "MatpNode"
) -> None:
    async with Client(f"{bridge_base_url}/mcp") as client:
        await call(client, "search_merchants", query="restaurant")
        await call(client, "get_my_reservations", guest_name="Nobody")

    logs = matp_node.logs()
    assert '"DISCOVER"' in logs
    assert '"RECALL"' in logs


async def test_node_down_gives_the_fallback_url_and_coming_back_needs_no_restart(
    bridge_base_url: str, matp_node: "MatpNode"
) -> None:
    async with Client(f"{bridge_base_url}/mcp") as client:
        assert "slots" in await call(client, "get_availability", **availability())
        matp_node.stop()
        try:
            direct = await call(client, "get_availability", **availability())
            named = await call(client, "search_merchants", query="a table at Le Petit Zinc")
            generic = await call(client, "search_merchants", query="restaurant tonight")
            mine = await call(client, "get_my_reservations", guest_name="Nobody")
        finally:
            matp_node.start()
        back = await call(client, "get_availability", **availability())

    assert direct["error"] == "agent_unavailable"
    assert direct["fallback_url"] == ZINC_FALLBACK
    assert named["error"] == "agent_unavailable"
    assert named["fallback_url"] == ZINC_FALLBACK
    assert generic["error"] == "agent_unavailable"
    assert generic["fallback_url"] is None
    assert mine["error"] == "agent_unavailable"
    assert "slots" in back


async def test_slow_node_times_out_within_the_budget_and_the_session_survives(
    bridge_base_url: str, matp_node: "MatpNode"
) -> None:
    async with Client(f"{bridge_base_url}/mcp") as client:
        assert "slots" in await call(client, "get_availability", **availability())
        matp_node.freeze()
        try:
            started = time.perf_counter()
            slow = await call(client, "get_availability", **availability())
            elapsed = time.perf_counter() - started
        finally:
            matp_node.resume()
        back = await call(client, "get_availability", **availability())

    assert slow["error"] == "agent_unavailable"
    assert slow["fallback_url"] == ZINC_FALLBACK
    assert 1.5 < elapsed < 3
    assert "slots" in back
