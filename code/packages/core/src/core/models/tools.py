"""The tools' contracts: the user tools (gateway), the admin and Studio tools (platform), the
build's output, the tool errors and the platform's `GET /session`."""

from datetime import date as Date
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    Field,
    HttpUrl,
    PositiveInt,
    StringConstraints,
    model_validator,
)

from core.models.base import (
    HHMM,
    BookingDay,
    BookingTime,
    DisplayFont,
    ErrorCode,
    FloorPlan,
    GuestText,
    Hex,
    MerchantProfile,
    NonEmptyStr,
    PriceLevel,
    Reservation,
    ReservationStatus,
    Service,
    Services,
    Slot,
    Slug,
    Tag,
    Via,
    _Model,
)

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


# The most start times an availability search returns (AGENTS.md §6).
MAX_SLOTS = 3


class GetAvailabilityInput(_Model):
    merchant: Slug
    date: BookingDay
    time: HHMM | None = None
    party_size: PositiveInt


class GetAvailabilityOutput(_Model):
    slots: tuple[Slot, ...] = Field(max_length=MAX_SLOTS)


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


class PublishAgentInput(_Model):
    """`publish_agent` takes nothing: it publishes the session's merchant (#136)."""


# Studio's requests (#152): typed inputs, the code does the rest.


class UpdateRulesInput(_Model):
    """`update_rules`: the cancellation policy guests read; empty removes it. The largest group
    stays `update_policy`'s, so that one request never has two tools (#152)."""

    cancellation_policy: Annotated[str, StringConstraints(max_length=300)]


class TryRequestInput(_Model):
    """`try_request`: what an agent asking for this party would get."""

    party_size: PositiveInt
    date: BookingDay | None = None  # the merchant's today when absent
    time: HHMM | None = None


class SendTestBookingInput(_Model):
    """`send_test_booking` takes nothing: a booking made as an agent would, to try the flow."""


class UpdateLookInput(_Model):
    """`update_look`: the management site's look; `reset` goes back to the site's own."""

    accent: Hex | None = None
    accent_2: Hex | None = None
    display_font: DisplayFont | None = None
    corners: Literal["organic", "rounded", "square"] | None = None
    reset: bool = False


# A moment of the restaurant's day: Home shows what the merchant needs then (#154).
Moment = Literal["morning", "before", "during", "after"]


class ShowViewInput(_Model):
    """`show_view`: which pane of Studio's canvas to show; on `manage`, optionally at a moment
    of the day."""

    view: Literal["agent", "manage"]
    moment: Moment | None = None


# The management site's pages (web/src/management/pages.ts), Home and the Assistant aside.
ManagePage = Literal["reservations", "guests", "hours", "floor", "rules", "agent", "settings"]


class LinkPageInput(_Model):
    """`link_page`: the management page a reply points to (#140)."""

    page: ManagePage


FactKey = Literal[
    "name",
    "hours",
    "tags",
    "max_party_size",
    "cancellation_policy",
    "price_level",
    "booking_page",
    "look",
]


class FactBox(_Model):
    """Where a fact's quote sits on its page's screenshot, in the image's pixels (#151)."""

    page: str
    x: int
    y: int
    width: int
    height: int


class BuildFact(_Model):
    """One line of the review card (#136): what the profile says and where it came from."""

    key: FactKey
    label: str
    value: str
    # Path of the crawled page ("/", "/horaires.html"), "you" for the owner's description, None
    # for a default or a value no quote could be found for.
    source: str | None
    # The exact words found there; never a quote the crawled text doesn't hold (ADR-0003).
    quote: str | None
    # On the screenshot; None without a screenshot, or when the words can't be found on it.
    box: FactBox | None = None


class BuildFlag(_Model):
    """Something to check before publishing, written by code: a bold line and why (#136)."""

    title: str
    detail: str


class BuildAgentOutput(_Model):
    profile: MerchantProfile
    # A build stops after `provision` (#136): hidden from guests until the owner publishes.
    status: Literal["building"]
    assumptions: tuple[str, ...]
    facts: tuple[BuildFact, ...]
    flags: tuple[BuildFlag, ...]
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
