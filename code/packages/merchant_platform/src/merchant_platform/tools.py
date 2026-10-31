"""Admin tools of the platform assistant (AGENTS.md §8.2).

Schemas come from `core.models`; results go back to the model as JSON, and the card the
merchant sees is built here from the same result, never from the model's text.
"""

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

from agent.service import AgentService
from builder.build import AlreadyProvisioned
from builder.llm import ToolCall, ToolSpec
from core.errors import AgentError, AgentUnavailable, InvalidInput
from core.models import (
    AdminCancelInput,
    BuildAgentInput,
    BuildAgentOutput,
    ChatEvent,
    ConfirmationCard,
    ConfirmationCardData,
    ErrorCode,
    GetStatsInput,
    ListReservationsInput,
    MerchantCancelOutcome,
    PolicyCard,
    ProfileCard,
    ReservationsCard,
    ReservationsCardData,
    SlotInput,
    StatsCard,
    StatsCardData,
    StepEvent,
    ToolStartEvent,
    UpdatePolicyInput,
    UpdateTablesInput,
)
from merchant_platform.accounts import IssuedLink
from merchant_platform.sessions import Session

log = logging.getLogger("merchant_platform.tools")

# Pushes one event into the merchant's `/chat` stream.
Emit = Callable[[ChatEvent], Awaitable[None]]
# Runs the builder for the merchant's website and description, streaming its steps.
BuildAgent = Callable[
    [BuildAgentInput, Callable[[StepEvent], Awaitable[None]]], Awaitable[BuildAgentOutput]
]

NOT_ONBOARDED = "No agent yet: onboard the merchant first with build_agent."
ALREADY_ONBOARDED = "This merchant already has a live agent: use the other tools to change it."
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
        "Build and publish the merchant's booking agent from their website URL and a "
        "description of how they work (hours, tables, group size, diets, terrace, children, "
        "cancellation). Takes a few seconds; the merchant sees the progress and the profile.",
        "Building your agent…",
        BuildAgentInput,
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
        session.link = link.fingerprint if link else None
        # The building browser may claim its owner cookie once, shortly after (#132).
        session.claimable_until = time.monotonic() + CLAIM_SECONDS if link else None
        data = output.model_dump(mode="json", exclude={"tools"})
        # On the card only: the secret link never goes to the model or the history.
        return Outcome(
            {"shown_to_merchant": "profile card", **data},
            ProfileCard(data=output, login_url=link.url if link else None),
        )

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
        return f"{local:%H:%M} on {local:%a %-d %b}"
