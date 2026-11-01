"""Admin tools of the platform assistant (AGENTS.md §8.2).

Schemas come from `core.models`; results go back to the model as JSON, and the card the
merchant sees is built here from the same result, never from the model's text.
"""

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

from agent.service import AgentService
from agent.words import short_day
from builder.build import AlreadyProvisioned
from builder.llm import ToolCall, ToolSpec
from builder.look import (
    ACCENT_CONTRAST,
    CORNER_WORDS,
    DEFAULT_LOOK,
    FONT_WORDS,
    contrast,
    hex_of,
    readable,
    rgb_of,
)
from builder.look import describe as describe_look
from core.errors import AgentError, AgentUnavailable, InvalidInput, PartyTooLarge
from core.models import (
    AdminCancelInput,
    BuildAgentInput,
    BuildAgentOutput,
    ChatEvent,
    ConfirmationCard,
    ConfirmationCardData,
    DiffCard,
    DiffCardData,
    ErrorCode,
    GetStatsInput,
    LinkPageInput,
    ListReservationsInput,
    Look,
    MerchantCancelOutcome,
    PageCard,
    PageCardData,
    PolicyCard,
    ProfileCard,
    PublishAgentInput,
    ReservationsCard,
    ReservationsCardData,
    SendTestBookingInput,
    ShowViewInput,
    SitePageEvent,
    SlotInput,
    StatsCard,
    StatsCardData,
    StepEvent,
    ToolStartEvent,
    TryRequestInput,
    UpdateLookInput,
    UpdatePolicyInput,
    UpdateRulesInput,
    UpdateTablesInput,
    ViewCard,
    ViewCardData,
)
from merchant_platform.accounts import IssuedLink
from merchant_platform.overview import MerchantRecord
from merchant_platform.sessions import Session

log = logging.getLogger("merchant_platform.tools")

# Pushes one event into the merchant's `/chat` stream.
Emit = Callable[[ChatEvent], Awaitable[None]]
# Runs the builder for the merchant's website and description, streaming its steps.
BuildAgent = Callable[
    [BuildAgentInput, Callable[[StepEvent | SitePageEvent], Awaitable[None]]],
    Awaitable[BuildAgentOutput],
]

NOT_ONBOARDED = "No agent yet: onboard the merchant first with build_agent."
ALREADY_ONBOARDED = "This merchant already has an agent: use the other tools to change it."
# Studio's answers (#152), as in the mockup (documentation/design/poc-v10/sesame-studio.html).
PUBLISHED = (
    "You're live. Agents get real answers now. The sample bookings are gone, so your list stays "
    "empty until the first guest books."
)
POLICY_SAVED = "Saved. Claude reads this before it cancels for a guest."
HIDDEN = "Agents get a 404 until you publish. Do that first, and I'll try it for you."
TEST_HIDDEN = "Agents get a 404 until you publish. Do that first, and I'll send one through."
TEST_GUEST = "Test booking"
UNREVIEWED = (
    "Not yet: show the review card and wait for the merchant to say it's right before publishing."
)
LOOK_RESET = "Back to the look from your site."
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
class ToolResult:
    content: str
    is_error: bool = False
    # The §8.4 code of a failed call, for the log line; the model reads `content`.
    error: ErrorCode | None = None


def _merchant_spoke_since_build(session: Session) -> bool:
    """A session that built its merchant publishes only after a merchant message: the model
    can't publish in the turn that built, before the review card was read (#152)."""
    if session.built_at is None:
        return True
    return any(m.get("role") == "user" for m in session.messages[session.built_at :])


def _diff(label: str, before: str | None, after: str, *, view: Any = None) -> DiffCard:
    return DiffCard(data=DiffCardData(label=label, before=before, after=after, view=view))


def _error(code: ErrorCode, content: str) -> ToolResult:
    return ToolResult(content, is_error=True, error=code)


@dataclass(frozen=True)
class Outcome:
    """What a tool handler returns: data for the model, and the card for the merchant."""

    data: dict[str, Any]
    card: ChatEvent | None = None


@dataclass(frozen=True)
class _Tool:
    name: str
    description: str
    label: str
    input: type[BaseModel]


TOOLS = (
    _Tool(
        "build_agent",
        "Build the merchant's booking agent from their website URL and a description of how "
        "they work (hours, tables, group size, diets, terrace, children, cancellation). Takes a "
        "few seconds; the merchant sees the progress and the review card. The agent stays "
        "hidden from guests until publish_agent.",
        "Building your agent…",
        BuildAgentInput,
    ),
    _Tool(
        "publish_agent",
        "Publish the merchant's agent, built and reviewed: guests and their assistants can book "
        "it from now on. Only call it once the merchant said the review card is right.",
        "Publishing your agent…",
        PublishAgentInput,
    ),
    _Tool(
        "update_rules",
        "Change the cancellation policy guests and their assistants read (an empty one removes "
        "it). The largest group is update_policy's.",
        "Saving your rules…",
        UpdateRulesInput,
    ),
    _Tool(
        "try_request",
        "Show what an AI agent gets when it asks for a table: the times offered for a party "
        "size (and optionally a day and time), or the refusal with the booking page.",
        "Asking your agent…",
        TryRequestInput,
    ),
    _Tool(
        "send_test_booking",
        "Book a test table for 2 the way an AI agent would, so the merchant sees it arrive. "
        "Only once the agent is published, and only when the merchant asks for a test booking.",
        "Sending a test booking…",
        SendTestBookingInput,
    ),
    _Tool(
        "update_look",
        "Change the management site's look: main or second colour (#rrggbb), the headings' "
        "font (serif, sans or Fraunces Soft) or the corners (organic, rounded, square); or "
        "`reset` to go back to the look read from the merchant's website. Never the merchant's "
        "website, and never the agent site, which has no interface.",
        "Changing your look…",
        UpdateLookInput,
    ),
    _Tool(
        "show_view",
        "Show the merchant a pane of Studio: `agent` (what an AI agent reads and gets) or "
        "`manage` (the management site). With `moment`, the management site as it looks at "
        "that time of day: morning, before, during or after service.",
        "Opening the view…",
        ShowViewInput,
    ),
    _Tool(
        "link_page",
        "Put a link under your reply to the management site's page that shows or changes what "
        "you answered about: reservations, guests, hours (opening hours and blocked times), "
        "floor (tables), rules (booking rules: largest group, cancelling), agent, settings.",
        "Adding a link…",
        LinkPageInput,
    ),
    _Tool(
        "list_reservations",
        "List the merchant's bookings for one day (YYYY-MM-DD). The merchant sees them as a table.",
        "Checking the bookings…",
        ListReservationsInput,
    ),
    _Tool(
        "get_stats",
        "Bookings, guests, seats and blocked times for one day (YYYY-MM-DD). The merchant sees "
        "the figures.",
        "Checking the numbers…",
        GetStatsInput,
    ),
    _Tool(
        "block_slot",
        "Stop new bookings starting at one time (ISO 8601 with the local UTC offset given in "
        "your instructions). Existing bookings stay.",
        "Blocking the time slot…",
        SlotInput,
    ),
    _Tool(
        "unblock_slot",
        "Accept bookings again at a previously blocked time (ISO 8601 with the local UTC "
        "offset given in your instructions).",
        "Opening the time slot…",
        SlotInput,
    ),
    _Tool(
        "update_policy",
        "Change the largest party that can book online and/or the service hours. Pass null "
        "for what doesn't change.",
        "Updating your booking policy…",
        UpdatePolicyInput,
    ),
    _Tool(
        "update_tables",
        "Replace the floor plan with the full list of tables (label and seats).",
        "Updating your floor plan…",
        UpdateTablesInput,
    ),
    _Tool(
        "cancel_reservation",
        "Cancel a booking by reference (e.g. R-001). The guest is notified, unless the booking "
        "is over or was already cancelled: the result says which. Only call it after the "
        "merchant explicitly confirmed this cancellation.",
        "Cancelling the booking…",
        AdminCancelInput,
    ),
)
SPECS = tuple(ToolSpec(t.name, t.description, t.input.model_json_schema()) for t in TOOLS)
_BY_NAME = {tool.name: tool for tool in TOOLS}


# How long after a build its browser may claim the owner cookie (`POST /session/claim`).
CLAIM_SECONDS = 300
# The secret link of a merchant just built, for its owner (#132).
IssueLink = Callable[[str], Awaitable[IssuedLink]]
# Publishes a merchant a build left waiting, streaming the `deploy` step (#136).
Publish = Callable[[str, Emit], Awaitable[None]]
# The registry's row: the merchant's status and stored look (#152).
FindMerchant = Callable[[str], Awaitable[MerchantRecord | None]]
# Stores the management site's look; reads the merchant website's own again.
SaveLook = Callable[[str, Look], Awaitable[None]]
SiteLook = Callable[[str], Awaitable[Look | None]]


class AdminTools:
    """Runs the model's tool calls for one merchant session."""

    def __init__(
        self,
        agent: AgentService | None,
        build_agent: BuildAgent,
        tz: ZoneInfo,
        today: Callable[[], date],
        *,
        owned_merchant: str | None = None,
        issue_link: IssueLink | None = None,
        publish: Publish | None = None,
        merchant: FindMerchant | None = None,
        save_look: SaveLook | None = None,
        site_look: SiteLook | None = None,
    ) -> None:
        """`owned_merchant` (PLATFORM_MERCHANT) is the only existing merchant a session may
        take over by onboarding its website again (development only, §13, §15).
        `issue_link` gives a new merchant its owner's secret link (#132)."""
        self._agent = agent
        self._build_agent = build_agent
        self._tz = tz
        self._today = today
        self._owned_merchant = owned_merchant
        self._issue_link = issue_link
        self._publish = publish
        self._merchant = merchant
        self._save_look = save_look
        self._site_look = site_look

    async def run(self, session: Session, call: ToolCall, emit: Emit) -> ToolResult:
        """Runs one call and logs it (§12): tool, merchant, latency, and `ok` or the error code.

        Never the arguments or the result: they hold guest names (`list_reservations`)."""
        started = time.perf_counter()
        outcome = "cancelled"
        try:
            result = await self._run(session, call, emit)
            outcome = result.error or "ok"
            return result
        finally:
            log.info(
                "admin_tool",
                extra={
                    "tool": call.name if call.name in _BY_NAME else "unknown",
                    "merchant": session.merchant_slug,
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                    "outcome": outcome,
                },
            )

    async def _run(self, session: Session, call: ToolCall, emit: Emit) -> ToolResult:
        tool = _BY_NAME.get(call.name)
        if tool is None:
            return _error("invalid_input", f"Unknown tool {call.name!r}.")
        await emit(ToolStartEvent(tool=tool.name, label=tool.label))
        if tool.name == "build_agent" and session.merchant_slug is not None:
            return _error("invalid_input", ALREADY_ONBOARDED)
        if tool.name != "build_agent" and session.merchant_slug is None:
            return _error("invalid_input", NOT_ONBOARDED)
        try:
            arguments = tool.input.model_validate_json(call.arguments or "{}")
        except ValidationError as error:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}"
                for e in error.errors(include_url=False)
            )
            return _error("invalid_input", f"invalid_input: {problems}")
        try:
            outcome = await self._handle(session, tool.name, arguments, emit)
        except AlreadyProvisioned as error:
            if error.slug != self._owned_merchant:
                # Typing someone's URL must not hand over their bookings and settings.
                return _error(error.code, f"{error.code}: {SITE_TAKEN}")
            # The demo's merchant onboards again: work on its existing agent.
            session.merchant_slug = error.slug
            return _error(error.code, f"{error.code}: {error}")
        except AgentError as error:
            return _error(error.code, f"{error.code}: {error}")
        except Exception:
            log.exception("tool_failed", extra={"tool": tool.name})
            return _error("agent_unavailable", TOOL_FAILED)
        if outcome.card is not None:
            await emit(outcome.card)
        return ToolResult(json.dumps(outcome.data, default=str, ensure_ascii=False))

    async def _handle(
        self, session: Session, name: str, arguments: BaseModel, emit: Emit
    ) -> Outcome:
        if isinstance(arguments, BuildAgentInput):
            return await self._build(session, arguments, emit)
        slug = session.merchant_slug
        assert slug is not None
        if isinstance(arguments, PublishAgentInput):
            if self._publish is None:
                raise AgentUnavailable(NOT_AVAILABLE)
            if not _merchant_spoke_since_build(session):
                raise InvalidInput(UNREVIEWED)
            await self._publish(slug, emit)
            return self._confirmation(PUBLISHED)
        if isinstance(arguments, UpdateLookInput):
            return await self._look(slug, arguments, emit)
        if isinstance(arguments, LinkPageInput):
            return Outcome(
                {"shown_to_merchant": "page link", "text": PAGE_LINKS[arguments.page]},
                PageCard(data=PageCardData(page=arguments.page)),
            )
        if isinstance(arguments, ShowViewInput):
            return Outcome(
                {
                    "shown_to_merchant": arguments.view,
                    "moment": arguments.moment,
                    "text": VIEWS[arguments.view],
                },
                ViewCard(data=ViewCardData(view=arguments.view, moment=arguments.moment)),
            )
        agent = self._require_agent()
        match name, arguments:
            case "list_reservations", ListReservationsInput(date=day):
                rows = tuple(await agent.list_reservations(slug, day))
                data = ReservationsCardData(date=day, reservations=rows)
                return Outcome(
                    {"shown_to_merchant": "bookings table", **data.model_dump(mode="json")},
                    ReservationsCard(data=data),
                )
            case "get_stats", GetStatsInput(date=day):
                stats = await agent.get_stats(slug, day)
                card_data = StatsCardData(date=day, **stats.model_dump())
                return Outcome(
                    {"shown_to_merchant": "figures", **card_data.model_dump(mode="json")},
                    StatsCard(data=card_data),
                )
            case "block_slot", SlotInput(starts_at=starts_at):
                self._check_offset(starts_at)
                await agent.block_slot(slug, starts_at)
                return self._confirmation(f"{self._when(starts_at)} blocked: no new bookings.")
            case "unblock_slot", SlotInput(starts_at=starts_at):
                self._check_offset(starts_at)
                await agent.unblock_slot(slug, starts_at)
                return self._confirmation(f"{self._when(starts_at)} open for bookings again.")
            case "update_policy", UpdatePolicyInput(max_party_size=size, services=services):
                policy = await agent.update_policy(slug, size, services)
                return Outcome(
                    {"shown_to_merchant": "policy", **policy.model_dump(mode="json")},
                    PolicyCard(data=policy),
                )
            case "update_tables", UpdateTablesInput(tables=tables):
                saved = await agent.update_tables(slug, tables)
                seats = sum(table.seats for table in saved)
                return self._confirmation(
                    f"Floor plan updated: {len(saved)} tables · {seats} seats."
                )
            case "update_rules", UpdateRulesInput():
                return await self._rules(slug, arguments, agent)
            case "try_request", TryRequestInput():
                return await self._try(slug, arguments, agent)
            case "send_test_booking", SendTestBookingInput():
                return await self._test_booking(slug, agent)
            case "cancel_reservation", AdminCancelInput(reservation_id=reservation_id):
                done = await agent.cancel_by_merchant(slug, reservation_id)
                return self._confirmation(CANCELLED[done.outcome].format(id=done.reservation.id))
        raise AssertionError(f"no handler for {name}")

    async def _build(self, session: Session, arguments: BuildAgentInput, emit: Emit) -> Outcome:
        output = await self._build_agent(arguments, emit)
        # The link first: if it can't be stored, the session isn't attached to a merchant
        # nobody owns (`make owner-link` then gives it one).
        link = await self._issue_link(output.profile.slug) if self._issue_link else None
        session.merchant_slug = output.profile.slug
        session.built_at = len(session.messages)
        session.link = link.fingerprint if link else None
        # The building browser may claim its owner cookie once, shortly after (#132).
        session.claimable_until = time.monotonic() + CLAIM_SECONDS if link else None
        # Never the facts: their values and quotes are the crawled site's own words, which must
        # not reach a model that holds write tools (ARCHITECTURE.md §4). It reads the flags,
        # which code writes; the merchant sees the facts on the card.
        data = output.model_dump(mode="json", exclude={"tools", "facts"})
        # On the card only: the secret link never goes to the model or the history.
        return Outcome(
            {"shown_to_merchant": "profile card", **data},
            ProfileCard(data=output, login_url=link.url if link else None),
        )

    async def _status(self, slug: str) -> str | None:
        record = await self._merchant(slug) if self._merchant else None
        return record.status if record else None

    async def _rules(self, slug: str, arguments: UpdateRulesInput, agent: AgentService) -> Outcome:
        before = await agent.get_rules(slug)
        policy = " ".join(arguments.cancellation_policy.split()) or None
        after = await agent.update_rules(
            slug, before.max_party_size, str(before.fallback_url), policy
        )
        return Outcome(
            {
                "shown_to_merchant": "change",
                "text": POLICY_SAVED,
                **after.model_dump(mode="json", exclude={"tools"}),
            },
            _diff("Cancelling", before.cancellation_policy, after.cancellation_policy or "None"),
        )

    async def _try(self, slug: str, arguments: TryRequestInput, agent: AgentService) -> Outcome:
        n = arguments.party_size
        if await self._status(slug) == "building":
            return self._confirmation(HIDDEN)
        day = arguments.date or self._today()
        try:
            slots = await agent.get_availability(slug, day, n, arguments.time)
        except PartyTooLarge:
            return self._confirmation(
                f"An agent asking for {n} gets a 413 with the link to your booking page, and the "
                "guest goes there."
            )
        if not slots:
            return self._confirmation(
                f"An agent asking for {n} on {short_day(day)} gets no time: every table that fits "
                "is taken or blocked."
            )
        times = [self._when_time(slot.starts_at) for slot in slots]
        listed = times[0] if len(times) == 1 else f"{', '.join(times[:-1])} and {times[-1]}"
        count = "1 time" if len(times) == 1 else f"{len(times)} times"
        return self._confirmation(f"An agent asking for {n} gets {count}: {listed}.")

    async def _test_booking(self, slug: str, agent: AgentService) -> Outcome:
        if await self._status(slug) == "building":
            return self._confirmation(TEST_HIDDEN)
        day = self._today()
        slots = await agent.get_availability(slug, day, 2)
        if not slots:
            slots = await agent.get_availability(slug, day + timedelta(days=1), 2)
        if not slots:
            return self._confirmation("No table for 2 is free today or tomorrow to try it on.")
        booked = await agent.create_reservation(
            slug, slots[0].starts_at, 2, TEST_GUEST, f"studio-test-{uuid4()}", via="claude"
        )
        return self._confirmation(
            f"Sent: {booked.id}, {short_day(slots[0].starts_at.astimezone(self._tz).date())} at "
            f"{self._when_time(slots[0].starts_at)} for 2, booked as an agent would. It's in your "
            "bookings: cancel it when you're done."
        )

    async def _look(self, slug: str, arguments: UpdateLookInput, emit: Emit) -> Outcome:
        if self._merchant is None or self._save_look is None:
            raise AgentUnavailable(NOT_AVAILABLE)
        record = await self._merchant(slug)
        if record is None:
            raise AgentUnavailable(NOT_AVAILABLE)
        current = record.look or DEFAULT_LOOK
        if arguments.reset:
            site = await self._site_look(slug) if self._site_look else None
            if site is None:
                return self._confirmation(NO_SITE_LOOK)
            await self._save_look(slug, site)
            return Outcome(
                {"shown_to_merchant": "change", "text": LOOK_RESET},
                _diff("Look", describe_look(current), describe_look(site), view="manage"),
            )
        changes: dict[str, Any] = {}
        texts: list[str] = []
        cards: list[DiffCard] = []
        bg = rgb_of(current.bg)
        for field, label in (("accent", "Main colour"), ("accent_2", "Second colour")):
            wanted = getattr(arguments, field)
            if wanted is None:
                continue
            used = hex_of(readable(rgb_of(wanted), bg, ACCENT_CONTRAST))
            changes[field] = used
            role = "main" if field == "accent" else "second"
            texts.append(
                f"{used} is your {role} colour now on the management site."
                if used == wanted
                else f"{wanted} is too faint on your background, so I adjusted it to {used}."
            )
            cards.append(_diff(label, getattr(current, field), used, view="manage"))
        if "accent" in changes:
            accent = rgb_of(changes["accent"])
            if contrast(rgb_of(current.on_accent), accent) < ACCENT_CONTRAST:
                white, dark = (255, 255, 255), (17, 17, 17)
                changes["on_accent"] = hex_of(
                    white if contrast(white, accent) >= contrast(dark, accent) else dark
                )
        if arguments.display_font is not None:
            changes["display_font"] = arguments.display_font
            texts.append(f"Your headings use {FONT_WORDS[arguments.display_font].lower()} now.")
            cards.append(
                _diff("Headings", current.display_font, arguments.display_font, view="manage")
            )
        if arguments.corners is not None:
            changes["corners"] = arguments.corners
            texts.append(f"{CORNER_WORDS[arguments.corners]} on the management site now.")
            cards.append(_diff("Corners", current.corners, arguments.corners, view="manage"))
        if not changes:
            raise InvalidInput("Say what to change: a colour, the headings' font or the corners.")
        updated = current.model_copy(update=changes)
        await self._save_look(slug, updated)
        for card in cards[:-1]:
            await emit(card)
        return Outcome(
            {"shown_to_merchant": "change", "text": " ".join(texts), "look": updated.model_dump()},
            cards[-1],
        )

    def _when_time(self, at: datetime) -> str:
        return at.astimezone(self._tz).strftime("%H:%M")

    def _require_agent(self) -> AgentService:
        if self._agent is None:
            raise AgentUnavailable(NOT_AVAILABLE)
        return self._agent

    def _confirmation(self, text: str) -> Outcome:
        return Outcome(
            {"shown_to_merchant": "confirmation", "text": text},
            ConfirmationCard(data=ConfirmationCardData(text=text)),
        )

    def _check_offset(self, starts_at: datetime) -> None:
        """Refuse a time whose offset isn't the merchant's at that instant (#83).

        A model sending the summer offset after the switch would otherwise block another hour
        without failing. A valid instant in another offset (`20:00Z` for 21:00 in winter) is
        refused too: the tool descriptions ask for the local offset, and the error names the
        time the model most likely meant.
        """
        expected = starts_at.astimezone(self._tz).utcoffset()
        if starts_at.utcoffset() == expected:
            return
        meant = starts_at.replace(tzinfo=self._tz)
        raise InvalidInput(
            f"{starts_at.isoformat()} doesn't use the {self._tz} offset, which is "
            f"UTC{meant:%:z} on {meant:%Y-%m-%d}. For {meant:%H:%M} local time, send "
            f"{meant.isoformat()}."
        )

    def _when(self, starts_at: datetime) -> str:
        local = starts_at.astimezone(self._tz)
        day = local.date()
        if day == self._today():
            return f"{local:%H:%M} tonight" if local.hour >= 17 else f"{local:%H:%M} today"
        return f"{local:%H:%M} on {short_day(day)}"
