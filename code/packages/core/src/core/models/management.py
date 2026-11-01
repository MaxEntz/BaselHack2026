"""The management site's contracts (#129): the platform's `/merchants/{slug}` routes."""

from datetime import date as Date
from typing import Annotated, Literal, Self, get_args

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BeforeValidator,
    Field,
    HttpUrl,
    PositiveInt,
    StringConstraints,
    model_validator,
)

from core.models.base import (
    HHMM,
    MAX_GUEST_TEXT,
    BookingTime,
    ErrorCode,
    GuestText,
    Look,
    NonEmptyStr,
    PriceLevel,
    ReservationStatus,
    Service,
    Slug,
    TableSpec,
    Tag,
    Via,
    _Model,
    _no_control_characters,
)
from core.models.tools import GeneratedTool, OpaqueId, TalkMessage, TalkOutput

# Management site (#129): the `/merchants/{slug}` routes of the platform. Keep in sync with
# web/src/types.ts. Local days and times are the merchant's (`MerchantOverview.timezone`).

# Merchant text typed in a form: no control character (#79), bounded like the free text a tool
# description may show (agent.provisioning.MAX_FREE_TEXT).
MAX_SHORT_TEXT = 80
MAX_POLICY_TEXT = 500
MAX_TABLE_LABEL = 20
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=MAX_SHORT_TEXT),
    AfterValidator(_no_control_characters),
]
# One paragraph: the web turns new lines into spaces.
PolicyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=MAX_POLICY_TEXT),
    AfterValidator(_no_control_characters),
]
SearchText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=MAX_GUEST_TEXT),
    AfterValidator(_no_control_characters),
]
TableLabel = Annotated[NonEmptyStr, StringConstraints(max_length=MAX_TABLE_LABEL)]
# Bounds of the forms: a day has at most 48 half-hour starts.
MAX_SERVICES = 8
MAX_TABLES = 100
MAX_DAY_STARTS = 48
# What Home, the search box and the Agent page show at most.
ACTIVITY_LINES = 6
SEARCH_LIMIT = 8
MAX_AGENT_REQUESTS = 10

# A published agent: `live`, or paused by its merchant (`down`, #146).
AgentStatus = Literal["live", "down"]
PUBLISHED_STATUSES: tuple[AgentStatus, ...] = get_args(AgentStatus)
# A merchant is `building` until its agent is published.
MerchantStatus = Literal["building", AgentStatus]
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
    "no_services",
    "service_incomplete",  # services: [i], a start or an end is missing
    "service_too_short",  # services: [i]
    "services_overlap",  # services: [i, j], i < j
    "no_tables",
    "duplicate_table",  # table
    "table_without_seats",  # table
    "too_many_seats",  # table, seats: the most a table may have
    "table_in_use",  # table, guest: a booking refers to a removed table
    "table_too_small",  # table, guest, seats: a booking needs more seats than it keeps
    "group_too_small",
    "name_required",
    "booking_page_off_site",  # the booking page must stay on the merchant's host
]
# `ErrorCode` stays the §8.4 list the tools answer; the management site adds its own.
ManagementErrorCode = (
    ErrorCode
    | Literal[
        "not_signed_in", "other_merchant", "needs_invite", "not_built_yet", "too_many_requests"
    ]
)


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
    now: AwareDatetime  # the server's clock, in `timezone`: its date is the merchant's today
    agent_address: str  # batp://<host>/<slug>
    mcp_url: str  # https://<host>/m/<slug>/mcp
    management_url: str  # https://<web host>/manage/<slug>
    owner_name: str | None  # what the owner asked to be called; None until known
    demo: bool  # built from one of the demo sites (code/sites/)
    # The site's look (#149) for the management site's tokens (#150); None: the default look.
    look: Look | None = None


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

    service: Service
    seats: int
    guests: int
    tables: tuple[RoomTable, ...]
    party_size: int  # the group `next_free` is for: 4, or the largest group when smaller
    next_free: HHMM | None  # never a blocked start
    largest_group: int  # min(max_party_size, largest table)


class ActivityItem(_Model):
    id: int
    at: AwareDatetime
    kind: ActivityKind
    # Worded by the server when read, relative to `Dashboard.date`:
    # "R-009, Sara Muller, 2 guests, today 18:30".
    text: str
    source: Via  # who made the change: an agent, the website or the merchant
    reservation_id: str | None


class Dashboard(_Model):
    """`GET /dashboard`: Home, for the merchant's today by the server's clock (`date`). A
    `?date=` other than that day is `invalid_input`."""

    date: Date
    history: tuple[DayHistory, ...]  # the 30 days ending `date`, oldest first
    next_days: tuple[DayGuests, ...]  # the 7 days starting `date`
    channels: tuple[ChannelCount, ...]  # bookings over `history`
    room: Room
    activity: tuple[ActivityItem, ...] = Field(max_length=ACTIVITY_LINES)  # newest first


class ReservationDay(_Model):
    """`GET /reservations?date=`: everything the Reservations page shows for one day."""

    date: Date
    services: tuple[Service, ...]  # by start
    starts: tuple[HHMM, ...]  # every start of the day on the grid, past ones included
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
    """`PATCH /reservations/{id}`: a booking that hasn't started, to a start of the same day;
    the table is picked again."""

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
    bookings: int  # live bookings in the next 7 days that haven't started
    via: Via


class GuestsOutput(_Model):
    """`GET /guests?q=`: guests with a live booking in the next 7 days, by name."""

    guests: tuple[GuestRow, ...]


class SearchOutput(_Model):
    """`GET /search?q=`: bookings of the next 7 days by guest name or reference."""

    reservations: tuple[ManagedReservation, ...] = Field(max_length=SEARCH_LIMIT)


class Hours(_Model):
    services: tuple[Service, ...]  # by start


# Form bodies are structural: their rules are checked by `core.forms`, which answers the
# `Problem` the web words, where a validating type would only give a generic 422.


def _blank_is_missing(value: object) -> object:
    # A cleared time input sends "": the form answers `service_incomplete`, not a pattern error.
    return None if isinstance(value, str) and not value.strip() else value


DraftTime = Annotated[HHMM | None, BeforeValidator(_blank_is_missing)]


class ServiceDraft(_Model):
    start: DraftTime = None
    end: DraftTime = None


class HoursInput(_Model):
    services: tuple[ServiceDraft, ...] = Field(max_length=MAX_SERVICES)


class FloorPlanOutput(_Model):
    tables: tuple[TableSpec, ...]


class TableDraft(_Model):
    label: TableLabel
    seats: int


class FloorPlanInput(_Model):
    tables: tuple[TableDraft, ...] = Field(max_length=MAX_TABLES)


class Rules(_Model):
    max_party_size: PositiveInt
    slot_length_min: int  # read-only
    fallback_url: HttpUrl
    cancellation_policy: str | None
    tools: tuple[GeneratedTool, ...]  # what an AI agent reads on /m/<slug>/mcp


class RulesInput(_Model):
    max_party_size: int
    fallback_url: HttpUrl
    cancellation_policy: PolicyText | None = None  # blank: none


class MerchantSettings(_Model):
    name: str
    city: str | None
    website: HttpUrl  # read-only
    price_level: PriceLevel
    tags: tuple[Tag, ...]


class SettingsInput(_Model):
    name: ShortText
    city: ShortText | None = None  # blank: none
    price_level: PriceLevel
    tags: tuple[Tag, ...] = Field(max_length=len(get_args(Tag)))


class BlockedTime(_Model):
    starts_at: AwareDatetime
    date: Date
    time: HHMM


class BlockedTimes(_Model):
    """`GET /blocked`, and the answer to `PUT /blocked?date=`: blocked times to come."""

    blocked: tuple[BlockedTime, ...]


class BlockedDayInput(_Model):
    """`PUT /blocked?date=`: the day's whole set, as local times. The last write wins."""

    times: tuple[HHMM, ...] = Field(max_length=MAX_DAY_STARTS)


class AgentStatusOutput(_Model):
    status: MerchantStatus


class SitePage(_Model):
    """A crawled page's screenshot, as Studio shows it (#151). Without images or web fonts."""

    path: str
    width: int
    height: int
    # A data: URL.
    image: str


class PublicSite(_Model):
    """`GET /site`: the public site as read during the build; no pages before #151."""

    pages: tuple[SitePage, ...]


AgentTryIntent = Literal["DISCOVER", "AVAILABILITY", "BOOK", "AMEND", "CANCEL", "RECALL", "PING"]


class AgentTryInput(_Model):
    """`POST /agent/try`: which frame to send; the platform fills in its params (#151)."""

    intent: AgentTryIntent


class AgentTalkInput(_Model):
    """`POST /agent/try` with a message: a real `TALK` to the merchant's agent (#253). `confirm`
    names a `pending` proposal, which only exists in a conversation."""

    message: TalkMessage
    conversation: OpaqueId | None = None
    confirm: OpaqueId | None = None

    @model_validator(mode="after")
    def validate_confirm_has_conversation(self) -> Self:
        if self.confirm is not None and self.conversation is None:
            raise ValueError("confirm needs the conversation of its proposal")
        return self


class AgentTryOutput(_Model):
    """Both frames, as sent and answered. `status` is the node's answer: 404 until published.
    `talk` is a `TALK`'s `200` answer, read from its frame; None for anything else."""

    request: str
    response: str
    status: int
    talk: TalkOutput | None = None


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

    requests: tuple[AgentRequest, ...] = Field(max_length=MAX_AGENT_REQUESTS)


class Problem(_Model):
    code: ProblemCode
    services: tuple[int, ...] = ()
    table: str | None = None
    guest: str | None = None
    seats: int | None = None


class ManagementError(_Model):
    """The `/merchants/{slug}` error body: a `ToolError`'s fields, with the management site's
    own codes and the form rule a request broke."""

    error: ManagementErrorCode
    message: str
    fallback_url: HttpUrl | None = None
    problem: Problem | None = None
