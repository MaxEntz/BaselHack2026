"""What the platform assistant's model reads about its admin tools (AGENTS.md §8.2), the texts
the merchant reads back, and what a tool's handler answers (`Outcome`).

Schemas come from `core.models`; a tool's result goes back to the model as JSON, and the card
the merchant sees is built from the same result, never from the model's text.
"""

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from core.llm import ToolSpec
from core.models import (
    AdminCancelInput,
    BuildAgentInput,
    ChatEvent,
    ConfirmationCard,
    ConfirmationCardData,
    DiffCard,
    DiffCardData,
    ErrorCode,
    GetStatsInput,
    LinkPageInput,
    ListReservationsInput,
    MerchantCancelOutcome,
    PublishAgentInput,
    SendTestBookingInput,
    ShowViewInput,
    SlotInput,
    TryRequestInput,
    UpdateKnowledgeInput,
    UpdateLookInput,
    UpdatePolicyInput,
    UpdateRulesInput,
    UpdateTablesInput,
)
from merchant_platform.trial import TEST_PARTY

NOT_ONBOARDED = "No agent yet: onboard the merchant first with build_agent."
ALREADY_ONBOARDED = "This merchant already has an agent: use the other tools to change it."
# Studio's answers (#152), as in the mockup (documentation/design/poc-v10/sesame-studio.html).
PUBLISHED = (
    "You're live. Agents get real answers now. The sample bookings are gone, so your list stays "
    "empty until the first guest books."
)
POLICY_SAVED = "Saved. Claude reads this before it cancels for a guest."
KNOWLEDGE = "What your agent knows"
KNOWLEDGE_SAVED = "Saved. Your agent answers guests with this from now on."
KNOWLEDGE_UNCHANGED = "Your agent held nothing like that, so nothing changed."
HIDDEN = "Agents get a 404 until you publish. Do that first, and I'll try it for you."
TEST_HIDDEN = "Agents get a 404 until you publish. Do that first, and I'll send one through."
UNREVIEWED = (
    "Not yet: show the review card and wait for the merchant to say it's right before publishing."
)
LOOK_RESET = "Back to the look from your site."
ACCENT_LABELS = {"accent": "Main colour", "accent_2": "Second colour"}
NO_SITE_LOOK = "I couldn't read your site's look again, so I kept this one."
# What the merchant sees under the link card (web: PageLink), for the model to stay in step.
PAGE_LINKS = {
    "reservations": "You can see them in Reservations.",
    "guests": "You can see them in Guests.",
    "hours": "You can change this in Opening hours.",
    "floor": "You can change this in Floor plan.",
    "rules": "You can change this in Booking rules.",
    "agent": "You can see what it did on the Agent page.",
    "settings": "You can change this in Settings.",
}
VIEWS = {
    "agent": "Here's your agent site: what an agent reads and gets back.",
    "manage": "Here's your management site.",
}
NOT_AVAILABLE = "Managing bookings isn't available yet on this platform."
TOOL_FAILED = "The tool failed unexpectedly."
SITE_TAKEN = (
    "This website already has an agent on the platform, managed from another account. "
    "It can't be built again from here."
)
# The merchant is told what a cancellation did, never a notice that wasn't stored (#82).
CANCELLED: dict[MerchantCancelOutcome, str] = {
    "notified": "{id} cancelled. The guest will be notified.",
    "ended": "{id} is in the past: cancelled, no notice sent.",
    "own_booking": "{id} cancelled. You made this booking, so no notice is sent.",
    "guest_cancelled": "{id} was already cancelled by the guest.",
    "already_cancelled": "{id} was already cancelled: no new notice sent.",
}


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    # What the merchant sees while it runs.
    label: str
    input: type[BaseModel]


TOOLS = (
    Tool(
        "build_agent",
        "Build the merchant's booking agent from their website URL and a description of how "
        "they work (hours, tables, group size, diets, terrace, children, cancellation). Takes a "
        "few seconds; the merchant sees the progress and the review card. The agent stays "
        "hidden from guests until publish_agent.",
        "Building your agent…",
        BuildAgentInput,
    ),
    Tool(
        "publish_agent",
        "Publish the merchant's agent, built and reviewed: guests and their assistants can book "
        "it from now on. Only call it once the merchant said the review card is right.",
        "Publishing your agent…",
        PublishAgentInput,
    ),
    Tool(
        "update_rules",
        "Change the cancellation policy guests and their assistants read (an empty one removes "
        "it). The largest group is update_policy's.",
        "Saving your rules…",
        UpdateRulesInput,
    ),
    Tool(
        "update_knowledge",
        "Correct what the merchant's agent tells guests beyond booking: the menu, drinks, "
        "parking, dogs, the chef. `remove` takes out every item holding these words; `add` states "
        'what is true now, in the merchant\'s words. "We no longer serve the tartare": remove '
        '"tartare". Hours, groups and tables have their own tools.',
        "Saving what your agent knows…",
        UpdateKnowledgeInput,
    ),
    Tool(
        "try_request",
        "Show what an AI agent gets when it asks for a table: the times offered for a party "
        "size (and optionally a day and time), or the refusal with the booking page.",
        "Asking your agent…",
        TryRequestInput,
    ),
    Tool(
        "send_test_booking",
        f"Book a test table for {TEST_PARTY} the way an AI agent would, so the merchant sees "
        "it arrive. "
        "Only once the agent is published, and only when the merchant asks for a test booking.",
        "Sending a test booking…",
        SendTestBookingInput,
    ),
    Tool(
        "update_look",
        "Change the management site's look: main or second colour (#rrggbb), the headings' "
        "font (serif, sans or Fraunces Soft) or the corners (organic, rounded, square); or "
        "`reset` to go back to the look read from the merchant's website. Never the merchant's "
        "website, and never the agent site, which has no interface.",
        "Changing your look…",
        UpdateLookInput,
    ),
    Tool(
        "show_view",
        "Show the merchant a pane of Studio: `agent` (what an AI agent reads and gets) or "
        "`manage` (the management site). With `moment`, the management site as it looks at "
        "that time of day: morning, before, during or after service.",
        "Opening the view…",
        ShowViewInput,
    ),
    Tool(
        "link_page",
        "Put a link under your reply to the management site's page that shows or changes what "
        "you answered about: reservations, guests, hours (opening hours and blocked times), "
        "floor (tables), rules (booking rules: largest group, cancelling), agent, settings.",
        "Adding a link…",
        LinkPageInput,
    ),
    Tool(
        "list_reservations",
        "List the merchant's bookings for one day (YYYY-MM-DD). The merchant sees them as a table.",
        "Checking the bookings…",
        ListReservationsInput,
    ),
    Tool(
        "get_stats",
        "Bookings, guests, seats and blocked times for one day (YYYY-MM-DD). The merchant sees "
        "the figures.",
        "Checking the numbers…",
        GetStatsInput,
    ),
    Tool(
        "block_slot",
        "Stop new bookings starting at one time (ISO 8601 with the local UTC offset given in "
        "your instructions). Existing bookings stay.",
        "Blocking the time slot…",
        SlotInput,
    ),
    Tool(
        "unblock_slot",
        "Accept bookings again at a previously blocked time (ISO 8601 with the local UTC "
        "offset given in your instructions).",
        "Opening the time slot…",
        SlotInput,
    ),
    Tool(
        "update_policy",
        "Change the largest party that can book online and/or the service hours. Pass null "
        "for what doesn't change.",
        "Updating your booking policy…",
        UpdatePolicyInput,
    ),
    Tool(
        "update_tables",
        "Replace the floor plan with the full list of tables (label and seats).",
        "Updating your floor plan…",
        UpdateTablesInput,
    ),
    Tool(
        "cancel_reservation",
        "Cancel a booking by reference (e.g. R-001). The guest is notified, unless the booking "
        "is over or was already cancelled: the result says which. Only call it after the "
        "merchant explicitly confirmed this cancellation.",
        "Cancelling the booking…",
        AdminCancelInput,
    ),
)
SPECS = tuple(ToolSpec(t.name, t.description, t.input.model_json_schema()) for t in TOOLS)
BY_NAME = {tool.name: tool for tool in TOOLS}


@dataclass(frozen=True)
class ToolResult:
    content: str
    is_error: bool = False
    # The §8.4 code of a failed call, for the log line; the model reads `content`.
    error: ErrorCode | None = None


def failed(code: ErrorCode, content: str) -> ToolResult:
    return ToolResult(content, is_error=True, error=code)


@dataclass(frozen=True)
class Outcome:
    """What a tool handler returns: data for the model, and the card for the merchant."""

    data: dict[str, Any]
    card: ChatEvent | None = None


def confirmation(text: str) -> Outcome:
    return Outcome(
        {"shown_to_merchant": "confirmation", "text": text},
        ConfirmationCard(data=ConfirmationCardData(text=text)),
    )


def diff(label: str, before: str | None, after: str, *, view: Any = None) -> DiffCard:
    return DiffCard(data=DiffCardData(label=label, before=before, after=after, view=view))
