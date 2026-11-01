"""The admin tools' dispatch (`merchant_platform.tools`) and their wording, without a model."""

from datetime import datetime, timedelta
from typing import Any

import pytest

from core.models import ChatEvent
from merchant_platform.bookings import BookingTools
from merchant_platform.sessions import Session
from merchant_platform.specs import SPECS, TOOL_FAILED
from merchant_platform.tools import AdminTools
from tests.unit.assistant_fakes import NOW, TODAY, TZ, call, fake_build, onboarded

# Valid arguments for each tool the model reads.
EXAMPLES: dict[str, dict[str, Any]] = {
    "build_agent": {"website": "https://zinc.example/", "description": "Dinner 19-22."},
    "publish_agent": {},
    "update_rules": {"cancellation_policy": "Free until 2 hours before."},
    "try_request": {"party_size": 2},
    "send_test_booking": {},
    "update_look": {"corners": "square"},
    "show_view": {"view": "agent"},
    "link_page": {"page": "hours"},
    "list_reservations": {"date": "2026-10-31"},
    "get_stats": {"date": "2026-10-31"},
    "block_slot": {"starts_at": "2026-10-31T21:00:00+01:00"},
    "unblock_slot": {"starts_at": "2026-10-31T21:00:00+01:00"},
    "update_policy": {"max_party_size": 6},
    "update_tables": {"tables": [{"label": "T1", "seats": 2}]},
    "cancel_reservation": {"reservation_id": "R-001"},
}


def test_every_tool_the_model_reads_has_an_example() -> None:
    assert set(EXAMPLES) == {spec.name for spec in SPECS}


@pytest.mark.parametrize("name", list(EXAMPLES))
async def test_every_tool_the_model_reads_reaches_its_handler(name: str) -> None:
    # A tool without a handler would fail unexpectedly; without its collaborators, each one
    # answers its own result or says what this platform lacks.
    tools = AdminTools(None, fake_build, TZ, today=lambda: TODAY)
    session = Session(id="s") if name == "build_agent" else onboarded()

    async def ignore(event: ChatEvent) -> None:
        pass

    result = await tools.run(session, call(name, **EXAMPLES[name]), ignore)

    assert result.content != TOOL_FAILED
    assert result.error in (None, "agent_unavailable")


def test_block_slot_confirmation_names_the_day() -> None:
    async def unknown(slug: str) -> None:
        return None

    tools = BookingTools(None, TZ, lambda: TODAY, unknown)
    tomorrow_noon = datetime.combine(TODAY + timedelta(days=1), datetime.min.time(), TZ).replace(
        hour=12
    )

    assert tools._when(tomorrow_noon) == "12:00 on Sun 1 Nov"
    assert tools._when(NOW.replace(hour=12, minute=0)) == "12:00 today"
