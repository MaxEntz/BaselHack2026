"""The merchant agent's tools refuse what the contracts refuse, as an error the model reads.

Once the gateway lost its booking tools (#254), these arguments come from the model: dates
Postgres or date arithmetic can't hold (#79), a change that changes nothing, a text with control
characters, an argument no tool takes. None of them may reach the service or end the turn.
"""

import json
from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest

from agent.guest_tools import GuestTools
from agent.service import AgentService
from core.llm import ToolCall

TZ = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 10, 31, 9, tzinfo=TZ)


class NoService:
    """Any call to the service fails the test."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the service was called: {name}")


def tools() -> GuestTools:
    service = cast(AgentService, NoService())
    return GuestTools(service, TZ, lambda: NOW, slug="le-petit-zinc", name="Le Petit Zinc")


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("get_availability", {"date": "9999-12-31", "party_size": 2}),
        ("get_availability", {"date": "2026-11-02", "party_size": 0}),
        ("get_availability", {"date": "2026-11-02", "time": "25:00", "party_size": 2}),
        (
            "propose_booking",
            {"starts_at": "0001-01-01T00:00:00+01:00", "party_size": 2, "guest_name": "Ada"},
        ),
        (
            "propose_booking",
            {"starts_at": "2026-11-02T20:00:00", "party_size": 2, "guest_name": "Ada"},
        ),
        (
            "propose_booking",
            {"starts_at": "2026-11-02T20:00:00+01:00", "party_size": 2, "guest_name": "Ada\x00"},
        ),
        ("propose_change", {"reservation_id": "R-001", "guest_name": "Ada"}),
        (
            "propose_change",
            {
                "reservation_id": "R-001",
                "guest_name": "Ada",
                "starts_at": "9999-12-31T23:30:00-05:00",
            },
        ),
        ("propose_cancellation", {"reservation_id": "R-\x00", "guest_name": "Ada"}),
        (
            "propose_cancellation",
            {"reservation_id": "R-001", "guest_name": "Ada", "merchant": "maison-albane"},
        ),
        ("create_reservation", {"starts_at": "2026-11-02T20:00:00+01:00"}),
    ],
    ids=[
        "far-future-day",
        "no-guests",
        "hour-25",
        "far-past-start",
        "start-without-offset",
        "name-nul",
        "change-of-nothing",
        "far-future-move",
        "id-nul",
        "another-merchant",
        "no-such-tool",
    ],
)
async def test_arguments_outside_the_contracts_are_an_invalid_input_the_model_reads(
    tool: str, arguments: dict[str, Any]
) -> None:
    outcome = await tools().run(ToolCall("call-1", tool, json.dumps(arguments)))

    assert outcome.is_error
    assert json.loads(outcome.content)["error"] == "invalid_input"
    assert outcome.proposal is None
    assert not outcome.to_website  # a refusal of the request, not of the restaurant


async def test_arguments_that_are_not_json_are_an_invalid_input_too() -> None:
    outcome = await tools().run(ToolCall("call-1", "get_availability", "{date: tomorrow"))

    assert outcome.is_error
    assert json.loads(outcome.content)["error"] == "invalid_input"
