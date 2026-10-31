"""`LocalAgentClient` fixes what must never come from Claude: `via` and `by`."""

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from gateway.agent_client import LocalAgentClient

STARTS_AT = datetime(2026, 11, 1, 19, tzinfo=UTC)


@pytest.fixture
def service() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def client(service: AsyncMock) -> LocalAgentClient:
    return LocalAgentClient(service)


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
