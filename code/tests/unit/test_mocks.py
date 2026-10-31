import json
from pathlib import Path
from typing import Any

from core.models import (
    AdminReservationRow,
    BuildAgentOutput,
    ListReservationsOutput,
    MerchantProfile,
)

MOCKS = Path(__file__).parents[2] / "web" / "src" / "mocks"


def test_onboarding_mock_matches_profile_contract(profile_data: dict[str, Any]) -> None:
    events = json.loads((MOCKS / "onboarding.json").read_text())
    steps = [event for event in events if event["type"] == "step"]

    assert [(event["step"], event["status"]) for event in steps] == [
        (step, status)
        for step in ["crawl", "extract", "validate", "provision", "deploy"]
        for status in ["running", "done"]
    ]
    card = next(event for event in events if event["type"] == "card")
    result = BuildAgentOutput.model_validate(card["data"])
    assert result.profile == MerchantProfile.model_validate(profile_data)
    assert result.model_dump(mode="json") == card["data"]
    assert events[-1] == {"type": "done"}


def test_tonight_and_notification_mocks_match_reservation_contract() -> None:
    events = json.loads((MOCKS / "tonight.json").read_text())
    card = next(event for event in events if event["type"] == "card")
    result = ListReservationsOutput.model_validate({"reservations": card["data"]["reservations"]})
    assert result.model_dump(mode="json")["reservations"] == card["data"]["reservations"]
    assert events[-1] == {"type": "done"}

    notification = json.loads((MOCKS / "notification.json").read_text())
    row = AdminReservationRow.model_validate(notification["reservation"])
    assert row in result.reservations
    assert notification["kind"] == "new_booking"
    assert notification["via"] == row.via == "claude"
