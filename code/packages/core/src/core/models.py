"""Shared input and output contracts for gateway and admin tools."""

from datetime import date as Date
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
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
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
    date: Date
    time: HHMM | None = None
    party_size: PositiveInt


class GetAvailabilityOutput(_Model):
    slots: tuple[Slot, ...] = Field(max_length=3)


class CreateReservationInput(_Model):
    merchant: Slug
    starts_at: AwareDatetime
    party_size: PositiveInt
    guest_name: NonEmptyStr
    idempotency_key: NonEmptyStr


class UpdateReservationInput(_Model):
    merchant: Slug
    reservation_id: NonEmptyStr
    guest_name: NonEmptyStr
    starts_at: AwareDatetime | None = None
    party_size: PositiveInt | None = None
    idempotency_key: NonEmptyStr

    @model_validator(mode="after")
    def validate_has_change(self) -> Self:
        if self.starts_at is None and self.party_size is None:
            raise ValueError("Provide starts_at or party_size")
        return self


class CancelReservationInput(_Model):
    merchant: Slug
    reservation_id: NonEmptyStr
    guest_name: NonEmptyStr


class ReservationOutput(_Model):
    reservation: Reservation


class CancelOutput(_Model):
    status: Literal["cancelled"]


class GetMyReservationsInput(_Model):
    guest_name: NonEmptyStr


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
    type: Literal["notification"] = "notification"
    kind: Literal["new_booking", "updated", "cancelled"]
    via: Via
    reservation: AdminReservationRow
