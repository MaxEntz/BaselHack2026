"""Both clients: what must never come from Claude is fixed, and BATP frames follow the spec."""

import json
import logging
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from batp.client import BatpClient, BatpRejected, SessionLost
from core.config import Settings
from core.db import make_engine
from core.errors import AgentUnavailable, NotFound
from core.logs import JsonFormatter
from core.models import SearchFilters, SearchMerchantsOutput
from gateway.agent_client import (
    BatpAgentClient,
    LocalAgentClient,
    make_agent_client,
    parse_batp_url,
)
from gateway.fallback import batp_request, log_call

STARTS_AT = datetime(2026, 11, 1, 19, tzinfo=UTC)
NO_ENGINE: Any = object()
ZINC = "le-petit-zinc"
RESERVATION = {
    "id": "R-001",
    "merchant": ZINC,
    "starts_at": "2026-11-01T20:00:00+01:00",
    "party_size": 2,
    "table_label": "T1",
    "status": "confirmed",
    "guest_name": "Alex",
    "via": "claude",
}


@pytest.fixture
def service() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def client(service: AsyncMock) -> LocalAgentClient:
    return LocalAgentClient(service, engine=NO_ENGINE)


async def test_bookings_from_claude_are_marked_as_such(
    client: LocalAgentClient, service: AsyncMock
) -> None:
    await client.create_reservation("le-petit-zinc", STARTS_AT, 2, "Alex", "key")

    service.create_reservation.assert_awaited_once_with(
        "le-petit-zinc", STARTS_AT, 2, "Alex", "key", via="claude"
    )


async def test_a_cancellation_from_claude_is_by_the_guest(
    client: LocalAgentClient, service: AsyncMock
) -> None:
    await client.cancel_reservation("le-petit-zinc", "R-001", "Alex")

    service.cancel_reservation.assert_awaited_once_with(
        "le-petit-zinc", "R-001", by="guest", guest_name="Alex"
    )


@pytest.mark.parametrize(
    ("method", "service_method", "args"),
    [
        (
            "get_availability",
            "get_availability",
            ("le-petit-zinc", date(2026, 11, 1), 2, "20:00"),
        ),
        (
            "update_reservation",
            "update_reservation",
            ("le-petit-zinc", "R-001", "Alex", "key", STARTS_AT, 3),
        ),
        ("get_my_reservations", "find_guest_reservations", ("Alex",)),
    ],
)
async def test_other_calls_are_passed_through(
    client: LocalAgentClient,
    service: AsyncMock,
    method: str,
    service_method: str,
    args: tuple[Any, ...],
) -> None:
    result = await getattr(client, method)(*args)

    getattr(service, service_method).assert_awaited_once_with(*args)
    assert result is getattr(service, service_method).return_value


async def test_local_search_goes_to_discovery(client: LocalAgentClient) -> None:
    found = SearchMerchantsOutput(merchants=())

    with patch("gateway.discovery.search", AsyncMock(return_value=found)) as search:
        result = await client.search_merchants("zinc", "Mulhouse", None, None)

    assert result is found
    search.assert_awaited_once_with("zinc", "Mulhouse", None, None, engine=NO_ENGINE)


@pytest.fixture
def node() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def bridge(node: AsyncMock) -> BatpAgentClient:
    return BatpAgentClient(node)


def sent(node: AsyncMock) -> tuple[str, str | None, dict[str, Any]]:
    intent, merchant, params = node.request.await_args.args
    return intent, merchant, params


async def test_search_is_a_discover_without_merchant(
    bridge: BatpAgentClient, node: AsyncMock
) -> None:
    node.request.return_value = {"merchants": [], "suggestions": [], "ignored_filters": []}

    await bridge.search_merchants(
        "table", None, date(2026, 11, 1), SearchFilters(tags=("vegan",), max_price="€€")
    )

    assert sent(node) == (
        "DISCOVER",
        None,
        {
            "query": "table",
            "date": "2026-11-01",
            "filters": {"tags": ["vegan"], "max_price": "€€"},
        },
    )


async def test_availability_carries_the_merchant_outside_its_params(
    bridge: BatpAgentClient, node: AsyncMock
) -> None:
    slot = {"starts_at": "2026-11-01T20:00:00+01:00", "table_label": "T5"}
    node.request.return_value = {"slots": [slot]}

    slots = await bridge.get_availability(ZINC, date(2026, 11, 1), 4, "20:00")

    assert sent(node) == (
        "AVAILABILITY",
        ZINC,
        {"date": "2026-11-01", "time": "20:00", "party_size": 4},
    )
    assert [s.table_label for s in slots] == ["T5"]


async def test_booking_is_a_book(bridge: BatpAgentClient, node: AsyncMock) -> None:
    node.request.return_value = {"reservation": RESERVATION}

    reservation = await bridge.create_reservation(ZINC, STARTS_AT, 2, "Alex", "key")

    assert sent(node) == (
        "BOOK",
        ZINC,
        {
            "starts_at": "2026-11-01T19:00:00Z",
            "party_size": 2,
            "guest_name": "Alex",
            "idempotency_key": "key",
        },
    )
    assert reservation.id == "R-001"


async def test_a_move_is_an_amend_with_only_what_changes(
    bridge: BatpAgentClient, node: AsyncMock
) -> None:
    node.request.return_value = {"reservation": RESERVATION}

    await bridge.update_reservation(ZINC, "R-001", "Alex", "key", party_size=3)

    assert sent(node) == (
        "AMEND",
        ZINC,
        {
            "reservation_id": "R-001",
            "guest_name": "Alex",
            "party_size": 3,
            "idempotency_key": "key",
        },
    )


async def test_a_cancellation_is_a_cancel(bridge: BatpAgentClient, node: AsyncMock) -> None:
    node.request.return_value = {"reservation": {**RESERVATION, "status": "cancelled"}}

    reservation = await bridge.cancel_reservation(ZINC, "R-001", "Alex")

    assert sent(node) == ("CANCEL", ZINC, {"reservation_id": "R-001", "guest_name": "Alex"})
    assert reservation.status == "cancelled"


async def test_my_reservations_is_a_recall(bridge: BatpAgentClient, node: AsyncMock) -> None:
    node.request.return_value = {"reservations": [RESERVATION], "notices": ["Sorry"]}

    reservations, notices = await bridge.get_my_reservations("Alex")

    assert sent(node) == ("RECALL", None, {"guest_name": "Alex"})
    assert [r.id for r in reservations] == ["R-001"]
    assert notices == ["Sorry"]


async def test_the_node_s_business_errors_pass_through(
    bridge: BatpAgentClient, node: AsyncMock
) -> None:
    node.request.side_effect = NotFound("No booking")

    with pytest.raises(NotFound):
        await bridge.cancel_reservation(ZINC, "R-001", "Alex")


@pytest.mark.parametrize(
    "failure", [SessionLost("gone"), BatpRejected(401, "Unknown agent or token")]
)
async def test_a_lost_or_refused_session_is_agent_unavailable(
    bridge: BatpAgentClient, node: AsyncMock, failure: Exception
) -> None:
    node.request.side_effect = failure

    with pytest.raises(AgentUnavailable):
        await bridge.get_availability(ZINC, date(2026, 11, 1), 2)


async def test_an_invalid_result_is_agent_unavailable_not_invalid_input(
    bridge: BatpAgentClient, node: AsyncMock
) -> None:
    node.request.return_value = {"unexpected": True}

    with pytest.raises(AgentUnavailable):
        await bridge.get_availability(ZINC, date(2026, 11, 1), 2)


async def test_the_request_id_reaches_the_node_and_the_log_line(
    bridge: BatpAgentClient, node: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    node.request.return_value = {"slots": []}

    with caplog.at_level(logging.INFO, logger="gateway.tools"):
        await bridge.get_availability(ZINC, date(2026, 11, 1), 2)
        log_call("get_availability", ZINC, 0.0, "result")

    request_id = node.request.await_args.kwargs["request_id"]
    line = json.loads(JsonFormatter().format(caplog.records[-1]))
    assert line["batp_request"] == request_id
    assert line["tool"] == "get_availability"
    batp_request.set(None)


async def test_close_closes_the_session(bridge: BatpAgentClient, node: AsyncMock) -> None:
    await bridge.close()

    node.close.assert_awaited_once_with()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("batp://127.0.0.1:4710", ("127.0.0.1", 4710)),
        ("batp://node.example", ("node.example", 4710)),
        ("batp://localhost:5000/", ("localhost", 5000)),
    ],
)
def test_parse_batp_url(url: str, expected: tuple[str, int]) -> None:
    assert parse_batp_url(url) == expected


@pytest.mark.parametrize("url", ["127.0.0.1:4710", "http://127.0.0.1:4710", "batp://"])
def test_a_wrong_batp_url_is_refused(url: str) -> None:
    with pytest.raises(ValueError, match="BATP_URL"):
        parse_batp_url(url)


def test_without_batp_url_the_agent_stays_in_this_process() -> None:
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@localhost/db")

    agent = make_agent_client(settings, make_engine(settings.database_url))

    assert isinstance(agent, LocalAgentClient)


def test_with_batp_url_the_gateway_is_a_bridge_under_its_own_identity() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://u:p@localhost/db",
        batp_url="batp://127.0.0.1:4711",
        batp_token="a-token-long-enough",
    )

    agent = make_agent_client(settings, make_engine(settings.database_url))

    assert isinstance(agent, BatpAgentClient)
    assert isinstance(agent.client, BatpClient)
    assert (agent.client.host, agent.client.port) == ("127.0.0.1", 4711)
    assert agent.client.token == "a-token-long-enough"
    assert agent.client.agent.id == "urn:batp:agent:claude-gateway"
    assert agent.client.agent.kind == "user-agent"
