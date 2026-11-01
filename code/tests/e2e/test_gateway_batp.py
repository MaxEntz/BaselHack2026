"""What only exists with the bridge: MCP → BATP → node, and that node dying or freezing (#61).

A booking crosses a node in this process whose merchant agent runs on a scripted model (#254).
`python -m merchant_node`, which can die or freeze, has no model: its talks get the fallback
URL, while its searches and the guest's bookings answer.
"""

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
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.discovery import find_named_merchant
from batp.client import BatpClient
from core.config import Settings
from core.models import SearchFilters, ToolError
from gateway.agent_client import GATEWAY_AGENT as GATEWAY_IDENTITY
from gateway.agent_client import BatpAgentClient, parse_batp_url
from gateway.fallback import call_service
from tests.e2e.conftest import BatpNode, book, call, running_gateway, talk
from tests.integration.agent_fakes import ScriptedLLM

pytestmark = pytest.mark.integration

ZINC = "le-petit-zinc"
ZINC_FALLBACK = "http://localhost:8080/reservation.html"
TZ = ZoneInfo("Europe/Zurich")
GATEWAY_AGENT = "urn:batp:agent:claude-gateway"
GOOD_TOKEN = "the-gateway-token-long-enough"
# The gateway's budget for a talk in the frozen-node test, instead of the 30 s default.
TALK_BUDGET_MS = 1500


def logged(caplog: pytest.LogCaptureFixture, message: str) -> list[dict[str, Any]]:
    return [vars(record) for record in caplog.records if record.getMessage() == message]


async def test_a_booking_crosses_the_node_under_the_gateway_s_identity(
    talking_gateway: str, agent_llm: ScriptedLLM, caplog: pytest.LogCaptureFixture
) -> None:
    guest = f"Alex {uuid4().hex[:8]}"
    day = datetime.now(TZ).date() + timedelta(days=1)
    starts_at = datetime.combine(day, datetime.strptime("19:30", "%H:%M").time(), TZ)

    with caplog.at_level(logging.INFO):
        reservation = await book(talking_gateway, agent_llm, ZINC, starts_at, guest)

    assert reservation["via"] == "claude"
    talks = [line for line in logged(caplog, "tool_call") if line["tool"] == "talk_to_merchant"]
    assert [line["outcome"] for line in talks] == ["ok", "ok"]
    crossed = {
        line["request"]: line["agent"]
        for line in logged(caplog, "batp request")
        if line["intent"] == "TALK"
    }
    assert {crossed.get(line["batp_request"]) for line in talks} == {GATEWAY_AGENT}
    # Neither the gateway's lines nor the node's carry the guest's name, extras included.
    assert not any(guest in str(vars(record)) for record in caplog.records)


async def test_every_tool_goes_through_the_node(bridge_base_url: str, batp_node: BatpNode) -> None:
    await call(bridge_base_url, "search_merchants", query="restaurant")
    await call(bridge_base_url, "get_my_reservations", guest_name="Nobody")
    answer = await talk(f"{bridge_base_url}/mcp", "Table for 2?", merchant=ZINC)

    logs = batp_node.logs()
    assert '"DISCOVER"' in logs
    assert '"RECALL"' in logs
    assert '"TALK"' in logs
    # This node has no model (#249): its 503 keeps the restaurant's link.
    assert (answer["error"], answer["fallback_url"]) == ("agent_unavailable", ZINC_FALLBACK)


async def test_node_down_gives_the_fallback_url_and_coming_back_needs_no_restart(
    bridge_base_url: str, batp_node: BatpNode
) -> None:
    assert (await call(bridge_base_url, "search_merchants", query="restaurant"))["merchants"]
    batp_node.stop()
    try:
        direct = await talk(f"{bridge_base_url}/mcp", "Table for 2?", merchant=ZINC)
        named = await call(bridge_base_url, "search_merchants", query="a table at Le Petit Zinc")
        generic = await call(bridge_base_url, "search_merchants", query="restaurant tonight")
        mine = await call(bridge_base_url, "get_my_reservations", guest_name="Nobody")
    finally:
        batp_node.start()
    back = await call(bridge_base_url, "search_merchants", query="restaurant tonight")

    assert direct["error"] == "agent_unavailable"
    assert direct["fallback_url"] == ZINC_FALLBACK
    assert named["error"] == "agent_unavailable"
    assert named["fallback_url"] == ZINC_FALLBACK
    assert generic["error"] == "agent_unavailable"
    assert generic["fallback_url"] is None
    assert mine["error"] == "agent_unavailable"
    assert back["merchants"]


async def test_slow_node_times_out_within_the_talk_s_budget_and_the_session_survives(
    batp_node: BatpNode, e2e_database_url: str
) -> None:
    settings = Settings(
        _env_file=None,
        database_url=e2e_database_url,
        batp_url=batp_node.url,
        agent_talk_timeout_ms=TALK_BUDGET_MS,
    )
    with running_gateway(settings) as gateway:
        assert (await call(gateway, "search_merchants", query="restaurant"))["merchants"]
        batp_node.freeze()
        try:
            started = time.perf_counter()
            slow = await talk(f"{gateway}/mcp", "Table for 2?", merchant=ZINC)
            elapsed = time.perf_counter() - started
        finally:
            batp_node.resume()
        back = await call(gateway, "search_merchants", query="restaurant")

    assert slow["error"] == "agent_unavailable"
    assert slow["fallback_url"] == ZINC_FALLBACK
    assert TALK_BUDGET_MS / 1000 < elapsed < 2 * TALK_BUDGET_MS / 1000
    assert back["merchants"]


async def test_a_burst_of_calls_over_one_session_is_never_invalid_input(
    batp_node: BatpNode,
) -> None:
    # 80 calls on one session, 32 at most in flight on the node: none is refused as invalid (#90).
    host, port = parse_batp_url(batp_node.url)
    agent = BatpAgentClient(BatpClient(host, port, GATEWAY_IDENTITY))
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@h/db")

    async def one() -> object:
        op = partial(agent.search_merchants, "restaurant", None, None, None)
        return await call_service("search_merchants", op, settings=settings)

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
                result = await call(url, "get_my_reservations", guest_name="Nobody")
            if works:
                assert result == {"reservations": [], "notices": []}
            else:
                assert result["error"] == "agent_unavailable"
                assert token not in result["message"]
    finally:
        node.stop()
    assert "wrong-token-long-enough" not in node.logs()
