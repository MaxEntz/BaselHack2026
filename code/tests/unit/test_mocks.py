import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from core.models import (
    BuildAgentOutput,
    ChatEvent,
    DoneEvent,
    LiveEvent,
    MerchantProfile,
    ReservationsCard,
)

MOCKS = Path(__file__).parents[2] / "web" / "src" / "mocks"
CHAT_EVENTS: TypeAdapter[list[ChatEvent]] = TypeAdapter(list[ChatEvent])


def load_mock(name: str) -> Any:
    return json.loads((MOCKS / f"{name}.json").read_text())


@pytest.mark.parametrize("name", ["onboarding", "tonight", "cards"])
def test_chat_mocks_match_python_events(name: str) -> None:
    events = load_mock(name)
    parsed = CHAT_EVENTS.validate_python(events)

    for event, raw in zip(parsed, events, strict=True):
        if raw["type"] == "card":
            assert event.model_dump(mode="json") == raw
    assert isinstance(parsed[-1], DoneEvent)


def test_onboarding_mock_matches_profile_contract(profile_data: dict[str, Any]) -> None:
    events = load_mock("onboarding")
    steps = [event for event in events if event["type"] == "step"]

    assert [(event["step"], event["status"]) for event in steps] == [
        (step, status)
        for step in ["crawl", "extract", "validate", "provision", "deploy"]
        for status in ["running", "done"]
    ]
    card = next(event for event in events if event["type"] == "card")
    result = BuildAgentOutput.model_validate(card["data"])
    assert result.profile == MerchantProfile.model_validate(profile_data)


def test_notification_mock_is_a_booking_from_tonight() -> None:
    card = next(event for event in load_mock("tonight") if event["type"] == "card")
    tonight = ReservationsCard.model_validate(card)

    notification = LiveEvent.model_validate(load_mock("notification"))

    assert notification.reservation in tonight.data.reservations
    assert notification.date == tonight.data.date
    assert notification.kind == "new_booking"
    assert notification.via == notification.reservation.via == "claude"
