"""Shared input and output contracts for gateway and admin tools."""

from datetime import date as Date
from typing import Annotated, Literal, Self

from pydantic import (
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
HHMM = Annotated[str, StringConstraints(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]{3,61}$")]
ErrorCode = Literal[
    "agent_unavailable",
    "party_too_large",
    "slot_unavailable",
    "not_found",
    "invalid_input",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Service(_Model):
    start: HHMM
    end: HHMM

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.end <= self.start:
            raise ValueError("Service end must be after start on the same day")
        return self


class TableSpec(_Model):
    label: str
    seats: PositiveInt


class Location(_Model):
    city: str | None = None
    lat: float | None = None
    lng: float | None = None


class MerchantProfile(_Model):
    slug: Slug
    name: str
    website: HttpUrl
    vertical: Literal["restaurant"]
    location: Location | None = None
    seats: PositiveInt
    services: list[Service] = Field(min_length=1)
    max_party_size: PositiveInt
    tables: list[TableSpec]
    tags: list[Tag]
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
    status: Literal["confirmed", "cancelled"]
    guest_name: str
    via: Via


# User tools


class SearchFilters(_Model):
    tags: list[str] = Field(default_factory=list)
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
    tags: list[Tag]
    price_level: PriceLevel
    website: HttpUrl


class FilterSuggestion(_Model):
    drop: str
    matches: int


class SearchMerchantsOutput(_Model):
    merchants: list[MerchantSummary]
    suggestions: list[FilterSuggestion] = Field(default_factory=list)
    ignored_filters: list[str] = Field(default_factory=list)


class GetAvailabilityInput(_Model):
    merchant: Slug
    date: Date
    time: HHMM | None = None
    party_size: PositiveInt


class GetAvailabilityOutput(_Model):
    slots: list[Slot] = Field(max_length=3)


class CreateReservationInput(_Model):
    merchant: Slug
    starts_at: AwareDatetime
    party_size: PositiveInt
    guest_name: str
    idempotency_key: str


class UpdateReservationInput(_Model):
    merchant: Slug
    reservation_id: str
    guest_name: str
    starts_at: AwareDatetime | None = None
    party_size: PositiveInt | None = None
    idempotency_key: str


class CancelReservationInput(_Model):
    merchant: Slug
    reservation_id: str
    guest_name: str


class ReservationOutput(_Model):
    reservation: Reservation


class CancelOutput(_Model):
    status: Literal["cancelled"]


class GetMyReservationsInput(_Model):
    guest_name: str


class GetMyReservationsOutput(_Model):
    reservations: list[Reservation]
    notices: list[str]


# Admin tools


class AdminReservationRow(_Model):
    id: str
    time: HHMM
    guest_name: str
    party_size: int
    table_label: str
    via: Via
    status: str


class ListReservationsInput(_Model):
    date: Date


class ListReservationsOutput(_Model):
    reservations: list[AdminReservationRow]


class GetStatsInput(_Model):
    date: Date


class Stats(_Model):
    bookings: int
    guests: int
    seats: int
    blocked: list[HHMM]


class SlotInput(_Model):
    starts_at: AwareDatetime


class OkOutput(_Model):
    ok: Literal[True] = True


class Policy(_Model):
    max_party_size: int
    slot_length_min: int
    services: list[Service]


class UpdatePolicyInput(_Model):
    max_party_size: PositiveInt | None = None
    services: list[Service] | None = None


class UpdateTablesInput(_Model):
    tables: list[TableSpec]


class AdminCancelInput(_Model):
    reservation_id: str


class GeneratedTool(_Model):
    name: str
    description: str


class BuildAgentInput(_Model):
    website: HttpUrl
    description: str


class BuildAgentOutput(_Model):
    profile: MerchantProfile
    status: Literal["live"]
    assumptions: list[str]
    tools: list[GeneratedTool]
    mcp_url: str


class ToolError(_Model):
    error: ErrorCode
    message: str
    fallback_url: HttpUrl | None = None
