"""The chat's events (SSE) and the assistant's conversation as the web shows it."""

from datetime import date as Date
from typing import Annotated, Literal

from pydantic import Field

from core.models.base import Via, _Model
from core.models.management import SitePage
from core.models.tools import (
    AdminReservationRow,
    BuildAgentOutput,
    ListReservationsOutput,
    ManagePage,
    Moment,
    Policy,
    Stats,
)

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
    # `waiting`: `deploy` after a build, until the owner publishes (#136).
    status: Literal["running", "done", "error", "waiting"]
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
    # The owner's secret link (#132), shown once: never part of the tool result the model reads.
    login_url: str | None = None


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


class DiffCardData(_Model):
    """A change, as code words it (ADR-0006): the old value struck through, the new one bold."""

    label: str
    before: str | None
    after: str
    view: Literal["agent", "manage"] | None = None  # the pane "See it" opens in Studio


class DiffCard(_Model):
    type: Literal["card"] = "card"
    kind: Literal["diff"] = "diff"
    data: DiffCardData


class ViewCardData(_Model):
    view: Literal["agent", "manage"]
    moment: Moment | None = None


class ViewCard(_Model):
    """Studio shows that pane of its canvas; the plain chat shows nothing (#152)."""

    type: Literal["card"] = "card"
    kind: Literal["view"] = "view"
    data: ViewCardData


class SitePageEvent(_Model):
    """A page the crawl read, sent as soon as it is done: Studio shows it during the build."""

    type: Literal["site_page"] = "site_page"
    page: SitePage


class PageCardData(_Model):
    page: ManagePage


class PageCard(_Model):
    """A link from a reply to a management page, worded by code (#140): "You can change this
    in Opening hours."."""

    type: Literal["card"] = "card"
    kind: Literal["page"] = "page"
    data: PageCardData


class ErrorEvent(_Model):
    type: Literal["error"] = "error"
    message: str


class DoneEvent(_Model):
    type: Literal["done"] = "done"


Card = Annotated[
    ProfileCard
    | ReservationsCard
    | StatsCard
    | PolicyCard
    | ConfirmationCard
    | DiffCard
    | ViewCard
    | PageCard,
    Field(discriminator="kind"),
]
ChatEvent = Annotated[
    TextEvent | ToolStartEvent | StepEvent | SitePageEvent | Card | ErrorEvent | DoneEvent,
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


# The assistant's conversation as the web shows it (#140), kept by the platform per session so
# that the management site's panel finds it after a reload or on another device.


class UserItem(_Model):
    type: Literal["user"] = "user"
    text: str


class AssistantItem(_Model):
    type: Literal["assistant"] = "assistant"
    text: str


class CardItem(_Model):
    type: Literal["card"] = "card"
    card: Card


class NotificationItem(_Model):
    type: Literal["notification"] = "notification"
    event: LiveEvent


class ErrorItem(_Model):
    type: Literal["error"] = "error"
    message: str


ConversationItem = Annotated[
    UserItem | AssistantItem | CardItem | NotificationItem | ErrorItem,
    Field(discriminator="type"),
]


class AssistantConversation(_Model):
    """`GET /merchants/{slug}/assistant`: the owner's conversation with the assistant. The
    session id is the platform's, one per owner link, so every device of the owner shares
    it; `/chat` still needs the owner's cookie for it."""

    session_id: str
    items: tuple[ConversationItem, ...]
