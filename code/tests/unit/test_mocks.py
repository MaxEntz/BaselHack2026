import json
import re
from datetime import datetime, time, timedelta
from itertools import combinations
from pathlib import Path
from typing import Any, get_args
from zoneinfo import ZoneInfo

import pytest
from pydantic import AwareDatetime, BaseModel, ConfigDict, TypeAdapter

from agent.provisioning import described_tools
from batp.messages import Request, Response
from core import models as core_models
from core.availability import bookable_starts
from core.models import (
    ActivityItem,
    AgentRequest,
    BlockedTime,
    BuildAgentOutput,
    ChannelCount,
    ChatEvent,
    DayHistory,
    DoneEvent,
    Hours,
    LiveEvent,
    ManagedReservation,
    MerchantOverview,
    MerchantProfile,
    MerchantSettings,
    ReservationsCard,
    Rules,
    SessionOutput,
    TableSpec,
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

    # A build stops after provision: deploy waits for the owner (#136).
    assert [(event["step"], event["status"]) for event in steps] == [
        (step, status)
        for step in ["crawl", "extract", "validate", "provision"]
        for status in ["running", "done"]
    ] + [("deploy", "waiting")]
    card = next(event for event in events if event["type"] == "card")
    result = BuildAgentOutput.model_validate(card["data"])
    assert result.profile == MerchantProfile.model_validate(profile_data)
    assert result.status == "building"


def test_session_mock_is_the_onboarded_merchant(profile_data: dict[str, Any]) -> None:
    session = SessionOutput.model_validate(load_mock("session"))

    assert session.merchant is not None
    assert (session.merchant.slug, session.merchant.name) == (
        profile_data["slug"],
        profile_data["name"],
    )


def test_notification_mock_is_a_booking_from_tonight() -> None:
    card = next(event for event in load_mock("tonight") if event["type"] == "card")
    tonight = ReservationsCard.model_validate(card)

    notification = LiveEvent.model_validate(load_mock("notification"))

    assert notification.reservation in tonight.data.reservations
    assert notification.date == tonight.data.date
    assert notification.kind == "new_booking"
    assert notification.via == notification.reservation.via == "claude"


# The management mock's data (#129), shared with the seed and the visual tests (#131).
FIXTURE = Path(__file__).parents[1] / "fixtures" / "demo" / "la-graine-verte-management.json"
PROFILE = Path(__file__).parents[1] / "fixtures" / "la-graine-verte.json"


class ManagementFixture(BaseModel):
    """`ManagementFixture` of web/src/types.ts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    now: AwareDatetime
    merchant: MerchantOverview
    hours: Hours
    tables: tuple[TableSpec, ...]
    rules: Rules
    settings: MerchantSettings
    history: tuple[DayHistory, ...]
    channels: tuple[ChannelCount, ...]
    reservations: tuple[ManagedReservation, ...]
    blocked: tuple[BlockedTime, ...]
    activity: tuple[ActivityItem, ...]
    agent_requests: tuple[AgentRequest, ...]
    live_booking: LiveEvent


@pytest.fixture(scope="module")
def management() -> ManagementFixture:
    return ManagementFixture.model_validate_json(FIXTURE.read_text())


def test_management_fixture_round_trips_through_the_models() -> None:
    raw = json.loads(FIXTURE.read_text())

    assert ManagementFixture.model_validate(raw).model_dump(mode="json") == raw


def test_management_fixture_is_la_graine_verte(management: ManagementFixture) -> None:
    profile = MerchantProfile.model_validate_json(PROFILE.read_text())

    assert management.merchant.slug == profile.slug
    assert management.tables == profile.tables
    assert management.hours.services == profile.services
    assert management.rules.max_party_size == profile.max_party_size
    assert management.rules.fallback_url == profile.fallback_url
    assert management.settings.website == management.merchant.website == profile.website
    # What an AI agent reads on /m/<slug>/mcp, as the merchant endpoint words it.
    assert management.rules.tools == tuple(described_tools(profile))


def test_management_history_is_the_30_days_ending_today(management: ManagementFixture) -> None:
    today = management.now.date()
    days = [entry.date for entry in management.history]

    assert days == [today - timedelta(days=30 - n) for n in range(1, 31)]
    live = [b for b in management.reservations if b.date == today and b.status == "confirmed"]
    assert management.history[-1] == DayHistory(
        date=today,
        bookings=len(live),
        guests=sum(b.party_size for b in live),
        agent_guests=sum(b.party_size for b in live if b.via == "claude"),
        cancelled=sum(
            1 for b in management.reservations if b.date == today and b.status == "cancelled"
        ),
    )
    assert sum(c.bookings for c in management.channels) == sum(
        entry.bookings for entry in management.history
    )


def test_management_bookings_fit_the_week_and_never_overlap(
    management: ManagementFixture,
) -> None:
    today = management.now.date()
    tz = ZoneInfo(management.merchant.timezone)
    length = timedelta(minutes=management.rules.slot_length_min)
    live_event = management.live_booking
    tonight = live_event.reservation
    booked = [
        *management.reservations,
        ManagedReservation(
            **tonight.model_dump(),
            date=live_event.date,
            cancelled_by=None,
            created_at=management.now,
        ),
    ]
    ids = [b.id for b in booked]
    seats = {table.label: table.seats for table in management.tables}

    assert len(set(ids)) == len(ids)
    for booking in booked:
        assert today <= booking.date < today + timedelta(days=7)
        starts = bookable_starts(management.hours.services, booking.date, tz)
        assert booking.time in {start.strftime("%H:%M") for start in starts}, booking.id
        assert booking.party_size <= seats[booking.table_label]
        assert (booking.cancelled_by is None) == (booking.status == "confirmed")

    def during(b: ManagedReservation) -> tuple[datetime, datetime]:
        start = datetime.combine(b.date, time.fromisoformat(b.time), tz)
        return start, start + length

    live = [b for b in booked if b.status == "confirmed"]
    for a, b in combinations(live, 2):
        if a.table_label == b.table_label:
            (a_start, a_end), (b_start, b_end) = during(a), during(b)
            assert a_end <= b_start or b_end <= a_start, (a.id, b.id)


def test_management_blocked_times_and_frames_are_consistent(
    management: ManagementFixture,
) -> None:
    tz = ZoneInfo(management.merchant.timezone)
    for blocked in management.blocked:
        local = blocked.starts_at.astimezone(tz)
        assert (local.date(), local.strftime("%H:%M")) == (blocked.date, blocked.time)

    for request in management.agent_requests:
        sent = Request.model_validate_json(request.request)
        answer = Response.model_validate_json(request.response)
        assert (sent.intent, answer.id, answer.status) == (request.intent, sent.id, request.status)
        assert request.refused == (request.status >= 400)
        assert "token" not in request.request


# Python and TypeScript drift (#129): the management models and their web types have the same
# fields. The fixture checks values; this checks the shapes no fixture holds.
TYPES = Path(__file__).parents[2] / "web" / "src" / "types.ts"
MIRRORED = [
    "MerchantOverview",
    "ManagedReservation",
    "DayHistory",
    "DayGuests",
    "ChannelCount",
    "RoomTable",
    "Room",
    "ActivityItem",
    "Dashboard",
    "ReservationDay",
    "MerchantBookingInput",
    "MoveBookingInput",
    "MerchantCancelOutput",
    "DaySlot",
    "DaySlots",
    "GuestRow",
    "GuestsOutput",
    "SearchOutput",
    "Hours",
    "ServiceDraft",
    "HoursInput",
    "FloorPlanOutput",
    "TableDraft",
    "FloorPlanInput",
    "Rules",
    "RulesInput",
    "MerchantSettings",
    "SettingsInput",
    "BlockedTime",
    "BlockedTimes",
    "AgentStatusOutput",
    "AgentRequest",
    "AgentRequests",
    "Problem",
    "ManagementError",
]


def ts_fields(name: str) -> dict[str, bool]:
    """Field name -> optional (`?`), for a flat `export type Name = { … };` of types.ts."""
    match = re.search(rf"^export type {name} = \{{(.*?)\}};", TYPES.read_text(), re.M | re.S)
    assert match, f"{name} is not a flat object type in types.ts"
    body = re.sub(r"//[^\n]*", "", match.group(1))  # comments may hold "word:"
    fields = re.findall(r"(\w+)(\??):", body)
    return {field: bool(optional) for field, optional in fields}


@pytest.mark.parametrize("name", MIRRORED)
def test_management_models_and_web_types_have_the_same_fields(name: str) -> None:
    model = getattr(core_models, name)

    python = {field: not info.is_required() for field, info in model.model_fields.items()}

    assert ts_fields(name) == python


LITERALS = [
    "MerchantStatus",
    "CancelledBy",
    "MerchantCancelOutcome",
    "ActivityKind",
    "ProblemCode",
    "ManagementErrorCode",
]


def ts_literals(name: str) -> set[str]:
    """The strings of a union of string literals in types.ts, following named members."""
    match = re.search(rf"^export type {name} =(.*?);", TYPES.read_text(), re.M | re.S)
    assert match, f"{name} is not a union in types.ts"
    body = re.sub(r"//[^\n]*", "", match.group(1))
    values = set(re.findall(r'"([^"]+)"', body))
    for member in re.findall(r"\|\s*([A-Z]\w*)", body):
        values |= ts_literals(member)
    return values


def py_literals(annotation: Any) -> set[str]:
    return {value for arg in get_args(annotation) for value in py_literals(arg)} | {
        arg for arg in get_args(annotation) if isinstance(arg, str)
    }


@pytest.mark.parametrize("name", LITERALS)
def test_management_unions_have_the_same_values(name: str) -> None:
    assert ts_literals(name) == py_literals(getattr(core_models, name))
