"""What only exists with the bridge: MCP → BATP → node, and that node dying or freezing (#61)."""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from mcp import Client
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.discovery import find_merchant, find_named_merchant
from batp.client import BatpClient
from core.config import Settings
from core.models import SearchFilters, ToolError
from gateway.agent_client import GATEWAY_AGENT as GATEWAY_IDENTITY
from gateway.agent_client import BatpAgentClient, parse_batp_url
from gateway.fallback import call_merchant, call_service
from tests.e2e.conftest import BatpNode, running_gateway

pytestmark = pytest.mark.integration

ZINC = "le-petit-zinc"
ZINC_FALLBACK = "http://localhost:8080/reservation.html"
TZ = ZoneInfo("Europe/Zurich")
GATEWAY_AGENT = "urn:batp:agent:claude-gateway"
GOOD_TOKEN = "the-gateway-token-long-enough"


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
    bridge_base_url: str, batp_node: BatpNode, caplog: pytest.LogCaptureFixture
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
    node_lines = [json.loads(line) for line in batp_node.logs().splitlines() if '"BOOK"' in line]
    assert any(
        line.get("agent") == GATEWAY_AGENT and line.get("request") == gateway_line["batp_request"]
        for line in node_lines
    ), batp_node.logs()
    assert guest not in batp_node.logs()
    assert guest not in caplog.text


async def test_every_tool_goes_through_the_node(bridge_base_url: str, batp_node: BatpNode) -> None:
    async with Client(f"{bridge_base_url}/mcp") as client:
        await call(client, "search_merchants", query="restaurant")
        await call(client, "get_my_reservations", guest_name="Nobody")

    logs = batp_node.logs()
    assert '"DISCOVER"' in logs
    assert '"RECALL"' in logs


async def test_node_down_gives_the_fallback_url_and_coming_back_needs_no_restart(
    bridge_base_url: str, batp_node: BatpNode
) -> None:
    async with Client(f"{bridge_base_url}/mcp") as client:
        assert "slots" in await call(client, "get_availability", **availability())
        batp_node.stop()
        try:
            direct = await call(client, "get_availability", **availability())
            named = await call(client, "search_merchants", query="a table at Le Petit Zinc")
            generic = await call(client, "search_merchants", query="restaurant tonight")
            mine = await call(client, "get_my_reservations", guest_name="Nobody")
        finally:
            batp_node.start()
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
    bridge_base_url: str, batp_node: BatpNode
) -> None:
    async with Client(f"{bridge_base_url}/mcp") as client:
        assert "slots" in await call(client, "get_availability", **availability())
        batp_node.freeze()
        try:
            started = time.perf_counter()
            slow = await call(client, "get_availability", **availability())
            elapsed = time.perf_counter() - started
        finally:
            batp_node.resume()
        back = await call(client, "get_availability", **availability())

    assert slow["error"] == "agent_unavailable"
    assert slow["fallback_url"] == ZINC_FALLBACK
    assert 1.5 < elapsed < 3
    assert "slots" in back


async def test_a_burst_of_calls_over_one_session_is_never_invalid_input(
    batp_node: BatpNode, e2e_engine: AsyncEngine
) -> None:
    # 80 calls on one session, 32 at most in flight on the node: none is refused as invalid (#90).
    host, port = parse_batp_url(batp_node.url)
    agent = BatpAgentClient(BatpClient(host, port, GATEWAY_IDENTITY))
    day = datetime.now(TZ).date() + timedelta(days=1)
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@h/db")

    async def one() -> object:
        find = partial(find_merchant, ZINC, engine=e2e_engine)
        op = partial(agent.get_availability, ZINC, day, 2, "20:00")
        return await call_merchant(ZINC, "get_availability", find, op, settings=settings)

    try:
        results = await asyncio.gather(*(one() for _ in range(80)))
    finally:
        await agent.close()

    errors = [result for result in results if isinstance(result, ToolError)]
    assert all(error.error == "agent_unavailable" for error in errors), errors
    assert len(errors) < len(results)
    assert "too many in flight" not in batp_node.logs()


@pytest.mark.parametrize(
    ("query", "tags"),
    [("a table for four tonight, " * 21, None), ("dinner", [f"t{n}" for n in range(33)])],
)
async def test_a_search_the_node_refuses_is_invalid_input_not_an_outage(
    batp_node: BatpNode, e2e_engine: AsyncEngine, query: str, tags: list[str] | None
) -> None:
    # Past the gateway's own check: the node answers 400, which must stay a refusal (#90).
    host, port = parse_batp_url(batp_node.url)
    agent = BatpAgentClient(BatpClient(host, port, GATEWAY_IDENTITY))
    filters = SearchFilters(tags=tuple(tags)) if tags else None
    named = partial(find_named_merchant, "Le Petit Zinc", engine=e2e_engine)
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@h/db")
    try:
        result = await call_service(
            "search_merchants",
            partial(agent.search_merchants, query, None, None, filters),
            settings=settings,
            named=named,
        )
    finally:
        await agent.close()

    assert isinstance(result, ToolError)
    assert result.error == "invalid_input"
    assert result.fallback_url is None


async def test_only_the_gateway_s_token_opens_a_session_on_a_secured_node(
    e2e_database_url: str, tmp_path: Path
) -> None:
    tokens = json.dumps({GATEWAY_AGENT: GOOD_TOKEN})
    node = BatpNode(e2e_database_url, tmp_path / "node.log", tokens=tokens)
    node.start()
    try:
        for token, works in ((GOOD_TOKEN, True), ("wrong-token-long-enough", False)):
            settings = Settings(
                _env_file=None,
                database_url=e2e_database_url,
                batp_url=node.url,
                batp_token=token,
            )
            with running_gateway(settings) as url:
                async with Client(f"{url}/mcp") as client:
                    result = await call(client, "get_availability", **availability())
            if works:
                assert "slots" in result
            else:
                assert result["error"] == "agent_unavailable"
                assert result["fallback_url"] == ZINC_FALLBACK
                assert token not in result["message"]
    finally:
        node.stop()
    assert "wrong-token-long-enough" not in node.logs()
