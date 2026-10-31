"""Admin tools of the platform assistant (AGENTS.md §8.2).

Schemas come from `core.models`; results go back to the model as JSON, and the card the
merchant sees is built here from the same result, never from the model's text.
"""

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

from agent.service import AgentService, MerchantCancelOutcome
from builder.build import AlreadyProvisioned
from builder.llm import ToolCall, ToolSpec
from core.errors import AgentError, AgentUnavailable
from core.models import (
    AdminCancelInput,
    BuildAgentInput,
    BuildAgentOutput,
    ChatEvent,
    ConfirmationCard,
    ConfirmationCardData,
    GetStatsInput,
    ListReservationsInput,
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
    "guest_cancelled": "{id} was already cancelled by the guest.",
    "already_cancelled": "{id} was already cancelled.",
}


@dataclass(frozen=True)
class ToolResult:
    content: str
    is_error: bool = False


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
        "Stop new bookings starting at one time (ISO 8601 with offset). Existing bookings stay.",
        "Blocking the time slot…",
        SlotInput,
    ),
    _Tool(
        "unblock_slot",
        "Accept bookings again at a previously blocked time (ISO 8601 with offset).",
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
    ) -> None:
        """`owned_merchant` (PLATFORM_MERCHANT) is the only existing merchant a session may
        take over by onboarding its website again: there is no authentication (§13, §15)."""
        self._agent = agent
        self._build_agent = build_agent
        self._tz = tz
        self._today = today
        self._owned_merchant = owned_merchant

    async def run(self, session: Session, call: ToolCall, emit: Emit) -> ToolResult:
        tool = _BY_NAME.get(call.name)
        if tool is None:
            return ToolResult(f"Unknown tool {call.name!r}.", is_error=True)
        await emit(ToolStartEvent(tool=tool.name, label=tool.label))
        if tool.name == "build_agent" and session.merchant_slug is not None:
            return ToolResult(ALREADY_ONBOARDED, is_error=True)
        if tool.name != "build_agent" and session.merchant_slug is None:
            return ToolResult(NOT_ONBOARDED, is_error=True)
        try:
            arguments = tool.input.model_validate_json(call.arguments or "{}")
        except ValidationError as error:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}"
                for e in error.errors(include_url=False)
            )
            return ToolResult(f"invalid_input: {problems}", is_error=True)
        try:
            outcome = await self._handle(session, tool.name, arguments, emit)
        except AlreadyProvisioned as error:
            if error.slug != self._owned_merchant:
                # Typing someone's URL must not hand over their bookings and settings.
                return ToolResult(f"{error.code}: {SITE_TAKEN}", is_error=True)
            # The demo's merchant onboards again: work on its existing agent.
            session.merchant_slug = error.slug
            return ToolResult(f"{error.code}: {error}", is_error=True)
        except AgentError as error:
            return ToolResult(f"{error.code}: {error}", is_error=True)
        except Exception:
            log.exception("tool_failed", extra={"tool": tool.name})
            return ToolResult(TOOL_FAILED, is_error=True)
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
                await agent.block_slot(slug, starts_at)
                return self._confirmation(f"{self._when(starts_at)} blocked: no new bookings.")
            case "unblock_slot", SlotInput(starts_at=starts_at):
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
        session.merchant_slug = output.profile.slug
        data = output.model_dump(mode="json", exclude={"tools"})
        return Outcome({"shown_to_merchant": "profile card", **data}, ProfileCard(data=output))

    def _require_agent(self) -> AgentService:
        if self._agent is None:
            raise AgentUnavailable(NOT_AVAILABLE)
        return self._agent

    def _confirmation(self, text: str) -> Outcome:
        return Outcome(
            {"shown_to_merchant": "confirmation", "text": text},
            ConfirmationCard(data=ConfirmationCardData(text=text)),
        )

    def _when(self, starts_at: datetime) -> str:
        local = starts_at.astimezone(self._tz)
        day = local.date()
        if day == self._today():
            return f"{local:%H:%M} tonight" if local.hour >= 17 else f"{local:%H:%M} today"
        return f"{local:%H:%M} on {local:%a %-d %b}"
