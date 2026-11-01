"""Shared types and the merchant's profile: what every contract is made of."""

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


Hex = Annotated[str, StringConstraints(pattern=r"^#[0-9a-f]{6}$")]
# The fonts the management site bundles (code/web), else the closest generic family (#149).
DisplayFont = Literal["Fraunces Soft", "serif", "sans"]
BodyFont = Literal["Karla", "serif", "sans"]


class Look(_Model):
    """The merchant site's colours and shapes, read from its homepage's computed styles by code,
    never by the LLM (#149, ADR-0003). The management site's tokens come from it (#150)."""

    bg: Hex  # the page
    surface: Hex  # cards
    line: Hex  # borders
    ink: Hex  # text
    muted: Hex  # secondary text
    accent: Hex
    accent_2: Hex
    on_accent: Hex  # text on either accent
    display_font: DisplayFont
    body_font: BodyFont
    corners: Literal["organic", "rounded", "square"]
    radius_px: Annotated[int, Field(ge=0, le=32)]  # cards and buttons; `rounded` uses it (#150)
    texture: Literal["leather", "none"]


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
    # Profiles stored before #149 have none: the management site then uses the default look.
    look: Look | None = None


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
