"""Shared input and output contracts for gateway and admin tools."""

import unicodedata
from datetime import date as Date
from datetime import datetime
from itertools import pairwise
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    PositiveInt,
    StringConstraints,
    model_validator,
)

Tag = Literal["gluten-free", "vegetarian", "vegan", "halal", "kosher", "terrace", "kid-friendly"]
PriceLevel = Literal["€", "€€", "€€€", "€€€€"]
Via = Literal["claude", "merchant", "website"]
ReservationStatus = Literal["confirmed", "cancelled"]
HHMM = Annotated[str, StringConstraints(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]{3,61}$")]


def _no_control_characters(value: str) -> str:
    # Postgres can't store NUL, and no name or key needs a control character (#79). Spaces,
    # non-breaking ones included, and letters of any script stay.
    if any(unicodedata.category(char) == "Cc" for char in value):
        raise ValueError("must not contain a control character")
    return value


NonEmptyStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
    AfterValidator(_no_control_characters),
]

# A guest's name, key or booking reference (#79): a 3 KB key would overflow the unique index,
# and a 2 MB name a BATP frame.
MAX_GUEST_TEXT = 200
GuestText = Annotated[NonEmptyStr, StringConstraints(max_length=MAX_GUEST_TEXT)]

# The days a guest may book or look up (#79): fixed, so the models never read the clock (the
# service's own clock refuses the past), and far from the ends of `date`, so the arithmetic on
# them (UTC conversion, end of a slot, the next day) can't overflow.
EARLIEST_BOOKING = Date(2000, 1, 1)
LATEST_BOOKING = Date(2099, 12, 31)


def _bookable_day(day: Date) -> Date:
    if not EARLIEST_BOOKING <= day <= LATEST_BOOKING:
        raise ValueError(f"must be between {EARLIEST_BOOKING} and {LATEST_BOOKING}")
    return day


def _bookable_time(moment: datetime) -> datetime:
    # Its own local date: converting 0001-01-01T00:00+01:00 to UTC would overflow.
    _bookable_day(moment.date())
    return moment


BookingDay = Annotated[Date, AfterValidator(_bookable_day)]
BookingTime = Annotated[AwareDatetime, AfterValidator(_bookable_time)]
ErrorCode = Literal[
    "agent_unavailable",
    "party_too_large",
    "slot_unavailable",
    "not_found",
    "invalid_input",
]

# AGENTS.md §6: last seating is the service end minus 60 minutes.
MIN_SERVICE_MINUTES = 60


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


class Service(_Model):
    start: HHMM
    end: HHMM

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if _minutes(self.end) - _minutes(self.start) < MIN_SERVICE_MINUTES:
            raise ValueError(
                f"Service must last at least {MIN_SERVICE_MINUTES} minutes on the same day"
            )
        return self


class TableSpec(_Model):
    label: NonEmptyStr
    seats: PositiveInt


def _no_overlap(services: tuple[Service, ...]) -> tuple[Service, ...]:
    ordered = sorted(services, key=lambda service: service.start)
    if any(later.start < earlier.end for earlier, later in pairwise(ordered)):
        raise ValueError("Services must not overlap")
    return services


def _valid_floor_plan(tables: tuple[TableSpec, ...]) -> tuple[TableSpec, ...]:
    if not tables:
        raise ValueError("At least one table is required")
    labels = [table.label for table in tables]
    if len(set(labels)) != len(labels):
        raise ValueError("Table labels must be unique")
    return tables


Services = Annotated[tuple[Service, ...], Field(min_length=1), AfterValidator(_no_overlap)]
# A validator rather than min_length: the schema must not push the LLM to invent a floor plan.
FloorPlan = Annotated[tuple[TableSpec, ...], AfterValidator(_valid_floor_plan)]


class Location(_Model):
    city: str | None = None
    lat: float | None = None
    lng: float | None = None


class MerchantProfile(_Model):
    slug: Slug
    name: NonEmptyStr
    website: HttpUrl
    vertical: Literal["restaurant"]
    location: Location | None = None
    seats: PositiveInt
    services: Services
    max_party_size: PositiveInt
    tables: FloorPlan
    tags: tuple[Tag, ...]
    price_level: PriceLevel
    cancellation_policy: str | None = None
    fallback_url: HttpUrl


class Slot(_Model):
    starts_at: AwareDatetime
    table_label: str


class Reservation(_Model):
    id: str
    merchant: Slug
    starts_at: AwareDatetime
    party_size: PositiveInt
    table_label: str
    status: ReservationStatus
    guest_name: str
    via: Via


# User tools


class SearchFilters(_Model):
    tags: tuple[str, ...] = ()
    max_price: PriceLevel | None = None


class SearchMerchantsInput(_Model):
    query: str
    near: str | None = None
    date: Date | None = None
    filters: SearchFilters | None = None


class MerchantSummary(_Model):
    slug: Slug
    name: str
    city: str | None
    tags: tuple[Tag, ...]
    price_level: PriceLevel
    website: HttpUrl


class FilterSuggestion(_Model):
    drop: str
    matches: int


class SearchMerchantsOutput(_Model):
    merchants: tuple[MerchantSummary, ...]
    suggestions: tuple[FilterSuggestion, ...] = ()
    ignored_filters: tuple[str, ...] = ()


class GetAvailabilityInput(_Model):
    merchant: Slug
    date: BookingDay
    time: HHMM | None = None
    party_size: PositiveInt


class GetAvailabilityOutput(_Model):
    slots: tuple[Slot, ...] = Field(max_length=3)


class CreateReservationInput(_Model):
    merchant: Slug
    starts_at: BookingTime
    party_size: PositiveInt
    guest_name: GuestText
    idempotency_key: GuestText


class UpdateReservationInput(_Model):
    merchant: Slug
    reservation_id: GuestText
    guest_name: GuestText
    starts_at: BookingTime | None = None
    party_size: PositiveInt | None = None
    idempotency_key: GuestText

    @model_validator(mode="after")
    def validate_has_change(self) -> Self:
        if self.starts_at is None and self.party_size is None:
            raise ValueError("Provide starts_at or party_size")
        return self


class CancelReservationInput(_Model):
    merchant: Slug
    reservation_id: GuestText
    guest_name: GuestText


class ReservationOutput(_Model):
    reservation: Reservation


class CancelOutput(_Model):
    status: Literal["cancelled"]


class GetMyReservationsInput(_Model):
    guest_name: GuestText


class GetMyReservationsOutput(_Model):
    reservations: tuple[Reservation, ...]
    notices: tuple[str, ...]


# Admin tools


class AdminReservationRow(_Model):
    id: str
    time: HHMM
    guest_name: str
    party_size: int
    table_label: str
    via: Via
    status: ReservationStatus


class ListReservationsInput(_Model):
    date: Date


class ListReservationsOutput(_Model):
    reservations: tuple[AdminReservationRow, ...]


class GetStatsInput(_Model):
    date: Date


class Stats(_Model):
    bookings: int
    guests: int
    seats: int
    blocked: tuple[HHMM, ...]


class SlotInput(_Model):
    starts_at: AwareDatetime


class OkOutput(_Model):
    ok: Literal[True] = True


class Policy(_Model):
    max_party_size: int
    slot_length_min: int
    services: tuple[Service, ...]


class UpdatePolicyInput(_Model):
    max_party_size: PositiveInt | None = None
    services: Services | None = None

    @model_validator(mode="after")
    def validate_has_change(self) -> Self:
        if self.max_party_size is None and self.services is None:
            raise ValueError("Provide max_party_size or services")
        return self


class UpdateTablesInput(_Model):
    tables: FloorPlan


class AdminCancelInput(_Model):
    reservation_id: NonEmptyStr


class GeneratedTool(_Model):
    name: str
    description: str


class BuildAgentInput(_Model):
    website: HttpUrl
    description: str


class BuildAgentOutput(_Model):
    profile: MerchantProfile
    status: Literal["live"]
    assumptions: tuple[str, ...]
    tools: tuple[GeneratedTool, ...]
    mcp_url: str


class ToolError(_Model):
    error: ErrorCode
    message: str
    fallback_url: HttpUrl | None = None


# Platform `GET /session`: keep in sync with web/src/types.ts


class SessionMerchant(_Model):
    slug: Slug
    name: str


class SessionOutput(_Model):
    """The merchant a chat session works on (#88); None while it is onboarding."""

    merchant: SessionMerchant | None


# Management site (#129): the `/merchants/{slug}` routes of the platform. Keep in sync with
# web/src/types.ts. Local days and times are the merchant's (`MerchantOverview.timezone`).

MerchantStatus = Literal["building", "live", "down"]
CancelledBy = Literal["guest", "merchant"]
# What a merchant's cancellation did (#82): cancelled with a notice for the guest; cancelled
# without one, since the booking is over or the merchant made it itself (#134); or nothing,
# since the guest or the merchant already cancelled it.
MerchantCancelOutcome = Literal[
    "notified", "ended", "own_booking", "guest_cancelled", "already_cancelled"
]
ActivityKind = Literal[
    "new_booking", "moved", "cancelled", "blocked", "reopened", "saved", "paused", "resumed"
]
# A form rule the server refused, precise enough for the web to word it (#133). `services`
# holds indices in the submitted list.
ProblemCode = Literal[
    "service_too_short",  # services: [i]
    "services_overlap",  # services: [i, j]
    "no_tables",
    "duplicate_table",  # table
    "table_without_seats",  # table
    "table_in_use",  # table, guest: a booking refers to a removed table
    "table_too_small",  # table, guest, seats: a booking needs more seats than it keeps
    "group_too_small",
    "name_required",
    "booking_page_off_site",  # the booking page must stay on the merchant's host
]


class Owner(_Model):
    name: str
    first_name: str


class MerchantOverview(_Model):
    """`GET /merchants/{slug}`. Never the stored `profile`: only what the pages show."""

    slug: Slug
    name: str
    city: str | None
    price_level: PriceLevel
    tags: tuple[Tag, ...]
    website: HttpUrl
    status: MerchantStatus
    timezone: str
    agent_address: str  # batp://<host>/<slug>
    mcp_url: str  # https://<host>/m/<slug>/mcp
    management_url: str  # /manage/<slug>
    owner: Owner | None
    demo: bool


class ManagedReservation(_Model):
    """A booking as the management site shows it, cancelled ones included."""

    id: str
    date: Date
    time: HHMM
    guest_name: str
    party_size: PositiveInt
    table_label: str
    via: Via
    status: ReservationStatus
    cancelled_by: CancelledBy | None
    created_at: AwareDatetime


class DayHistory(_Model):
    date: Date
    bookings: int  # live bookings for that day
    guests: int  # in those bookings
    agent_guests: int  # of which booked through an agent
    cancelled: int  # bookings for that day that were cancelled


class DayGuests(_Model):
    date: Date
    guests: int


class ChannelCount(_Model):
    via: Via
    bookings: int


class RoomTable(_Model):
    label: str
    seats: PositiveInt
    booked: bool  # has a live booking in the service


class Room(_Model):
    """Today's current or next service, else its last one."""

    service: Service | None  # None on a day without services
    seats: int
    guests: int
    tables: tuple[RoomTable, ...]
    party_size: int  # the group `next_free` is for: 4, or the largest group when smaller
    next_free: HHMM | None
    largest_group: int  # min(max_party_size, largest table)


class ActivityItem(_Model):
    id: int
    at: AwareDatetime
    kind: ActivityKind
    text: str  # written by the server: "R-009, Sara Muller, 2 guests, today 18:30"
    source: Via  # who made the change: an agent, the website or the merchant
    reservation_id: str | None


class Dashboard(_Model):
    """`GET /dashboard?date=`: Home. `date` is today."""

    date: Date
    history: tuple[DayHistory, ...]  # the 30 days ending `date`, oldest first
    next_days: tuple[DayGuests, ...]  # the 7 days starting `date`
    channels: tuple[ChannelCount, ...]  # bookings over `history`
    room: Room
    activity: tuple[ActivityItem, ...] = Field(max_length=6)  # newest first


class ReservationDay(_Model):
    """`GET /reservations?date=`: everything the Reservations page shows for one day."""

    date: Date
    services: tuple[Service, ...]
    slot_length_min: int
    largest_group: int
    blocked: tuple[HHMM, ...]
    reservations: tuple[ManagedReservation, ...]  # by time, cancelled ones included


class MerchantBookingInput(_Model):
    """`POST /reservations`: booked `via="merchant"`."""

    starts_at: BookingTime
    party_size: PositiveInt
    guest_name: GuestText
    idempotency_key: GuestText


class MoveBookingInput(_Model):
    """`PATCH /reservations/{id}`: same day only; the table is picked again."""

    starts_at: BookingTime
    idempotency_key: GuestText


class MerchantCancelOutput(_Model):
    reservation: ManagedReservation
    outcome: MerchantCancelOutcome


class DaySlot(_Model):
    starts_at: AwareDatetime
    time: HHMM
    until: HHMM  # the end of a booking starting then
    table: TableSpec | None  # the table a booking would get; None when none is free
    blocked: bool


class DaySlots(_Model):
    """`GET /slots?date=&party_size=&ignore=`: every start of the day that hasn't passed.

    `ignore` is a booking to leave out, for a move.
    """

    date: Date
    party_size: PositiveInt
    slots: tuple[DaySlot, ...]


class GuestRow(_Model):
    guest_name: str
    reservation_id: str  # the earliest live booking
    date: Date
    time: HHMM
    party_size: PositiveInt
    bookings: int  # live bookings in the next 7 days
    via: Via


class GuestsOutput(_Model):
    """`GET /guests?q=`: guests with a live booking in the next 7 days, by name."""

    guests: tuple[GuestRow, ...]


class SearchOutput(_Model):
    """`GET /search?q=`: bookings of the next 7 days by guest name or reference."""

    reservations: tuple[ManagedReservation, ...] = Field(max_length=8)


class Hours(_Model):
    services: tuple[Service, ...]


class HoursInput(_Model):
    services: Services


class FloorPlanOutput(_Model):
    tables: tuple[TableSpec, ...]


class Rules(_Model):
    max_party_size: PositiveInt
    slot_length_min: int  # read-only
    fallback_url: HttpUrl
    cancellation_policy: str | None
    tools: tuple[GeneratedTool, ...]  # what an AI agent reads on /m/<slug>/mcp


class RulesInput(_Model):
    max_party_size: PositiveInt
    fallback_url: HttpUrl
    cancellation_policy: str | None = None


class MerchantSettings(_Model):
    name: str
    city: str | None
    website: HttpUrl  # read-only
    price_level: PriceLevel
    tags: tuple[Tag, ...]


class SettingsInput(_Model):
    name: NonEmptyStr
    city: NonEmptyStr | None = None
    price_level: PriceLevel
    tags: tuple[Tag, ...]


class BlockedTime(_Model):
    starts_at: AwareDatetime
    date: Date
    time: HHMM


class BlockedTimes(_Model):
    """`GET /blocked`, and the answer to `PUT /blocked?date=`: blocked times to come."""

    blocked: tuple[BlockedTime, ...]


class BlockedDayInput(_Model):
    """`PUT /blocked?date=`: the day's whole set, as local times."""

    times: tuple[HHMM, ...]


class AgentStatusOutput(_Model):
    status: MerchantStatus


class AgentRequest(_Model):
    """One request an AI agent sent through BATP, with both frames as sent (#146)."""

    id: int
    at: AwareDatetime
    agent_name: str
    intent: str
    status: int
    refused: bool  # status >= 400
    title: str
    text: str
    request: str  # the request frame, JSON
    response: str  # the answer frame, JSON


class AgentRequests(_Model):
    """`GET /agent/requests?date=&refused=`: newest first, 10 at most."""

    requests: tuple[AgentRequest, ...] = Field(max_length=10)


class Problem(_Model):
    code: ProblemCode
    services: tuple[int, ...] = ()
    table: str | None = None
    guest: str | None = None
    seats: int | None = None


class ManagementError(ToolError):
    """The `/merchants/{slug}` error body: a `ToolError`, with the form rule it broke."""

    problem: Problem | None = None


# SSE events: keep in sync with web/src/types.ts


class TextEvent(_Model):
    type: Literal["text"] = "text"
    delta: str


class ToolStartEvent(_Model):
    type: Literal["tool_start"] = "tool_start"
    tool: str
    label: str


class StepEvent(_Model):
    type: Literal["step"] = "step"
    step: Literal["crawl", "extract", "validate", "provision", "deploy"]
    status: Literal["running", "done", "error"]
    detail: str | None = None
    duration_ms: int | None = None


class ReservationsCardData(ListReservationsOutput):
    date: Date


class StatsCardData(Stats):
    date: Date


class ConfirmationCardData(_Model):
    text: str


class ProfileCard(_Model):
    type: Literal["card"] = "card"
    kind: Literal["profile"] = "profile"
    data: BuildAgentOutput


class ReservationsCard(_Model):
    type: Literal["card"] = "card"
    kind: Literal["reservations"] = "reservations"
    data: ReservationsCardData


class StatsCard(_Model):
    type: Literal["card"] = "card"
    kind: Literal["stats"] = "stats"
    data: StatsCardData


class PolicyCard(_Model):
    type: Literal["card"] = "card"
    kind: Literal["policy"] = "policy"
    data: Policy


class ConfirmationCard(_Model):
    type: Literal["card"] = "card"
    kind: Literal["confirmation"] = "confirmation"
    data: ConfirmationCardData


class ErrorEvent(_Model):
    type: Literal["error"] = "error"
    message: str


class DoneEvent(_Model):
    type: Literal["done"] = "done"


Card = Annotated[
    ProfileCard | ReservationsCard | StatsCard | PolicyCard | ConfirmationCard,
    Field(discriminator="kind"),
]
ChatEvent = Annotated[
    TextEvent | ToolStartEvent | StepEvent | Card | ErrorEvent | DoneEvent,
    Field(discriminator="type"),
]


class LiveEvent(_Model):
    """A guest's change to a booking. The merchant's own changes are not sent (#81).

    `via` is the channel the booking was made through; `date` is its day in the
    merchant's timezone (`reservation.time` only has the hour).
    """

    type: Literal["notification"] = "notification"
    kind: Literal["new_booking", "updated", "cancelled"]
    via: Via
    date: Date
    reservation: AdminReservationRow
