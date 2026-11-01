"""The tools a merchant's AI agent may call (#246): thin wrappers over `AgentService`.

Code binds them to one merchant, so no tool takes a merchant and none reaches another merchant's
data, and no admin tool is ever among them (AGENTS.md §8.2). None of them writes (#247):
`propose_*` check the request and return a `Proposal`, which code runs only when the guest's
agent confirms it, never on the model's word.
"""

import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Self
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, PositiveInt, ValidationError, model_validator

from agent.service import AgentService
from agent.times import hhmm
from core.errors import AgentError, InvalidInput, NotFound, SlotUnavailable
from core.llm import ToolCall, ToolSpec
from core.models import (
    HHMM,
    MAX_PENDING_SUMMARY,
    BookingDay,
    BookingTime,
    GuestText,
    PendingAction,
    PendingKind,
    Reservation,
    Slot,
)

# How long a proposal waits for the guest's yes (#247).
PROPOSAL_TTL = timedelta(minutes=10)
# The refusals that send the guest to the merchant's own booking page (AGENTS.md §8.4).
SENDS_TO_WEBSITE = frozenset({"party_too_large", "agent_unavailable"})
WAITING = (
    "Prepared, not done: nothing changes until the guest's agent confirms it after the guest's "
    "yes. Give them this summary and ask."
)


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AvailabilityArgs(_Args):
    date: BookingDay
    time: HHMM | None = None
    party_size: PositiveInt


class BookingsArgs(_Args):
    guest_name: GuestText


class BookArgs(_Args):
    starts_at: BookingTime
    party_size: PositiveInt
    guest_name: GuestText


class ChangeArgs(_Args):
    reservation_id: GuestText
    guest_name: GuestText
    starts_at: BookingTime | None = None
    party_size: PositiveInt | None = None

    @model_validator(mode="after")
    def validate_has_change(self) -> Self:
        if self.starts_at is None and self.party_size is None:
            raise ValueError("Provide starts_at or party_size")
        return self


class CancelArgs(_Args):
    reservation_id: GuestText
    guest_name: GuestText


@dataclass(frozen=True)
class Proposal:
    """A write the agent prepared: its exact arguments, and the summary code wrote from them."""

    id: str
    action: PendingKind
    arguments: BookArgs | ChangeArgs | CancelArgs
    summary: str
    expires_at: datetime

    def pending(self) -> PendingAction:
        return PendingAction(
            id=self.id, action=self.action, summary=self.summary, expires_at=self.expires_at
        )


def _spec(name: str, description: str, args: type[BaseModel]) -> ToolSpec:
    return ToolSpec(name, description, args.model_json_schema())


SPECS: tuple[ToolSpec, ...] = (
    _spec(
        "get_availability",
        "The restaurant's free start times for a party on a day, up to 3, closest to `time` "
        "(HH:MM, local) when given. The only source of times you may offer.",
        AvailabilityArgs,
    ),
    _spec(
        "find_my_bookings",
        "The guest's upcoming bookings at this restaurant, by the name they booked under.",
        BookingsArgs,
    ),
    _spec(
        "propose_booking",
        "Prepare a booking once you know the party size, the exact start (with its UTC offset, "
        "from get_availability) and the guest's name. It books nothing: the guest's agent "
        "confirms it after the guest's yes.",
        BookArgs,
    ),
    _spec(
        "propose_change",
        "Prepare a change of the guest's booking (a new start, a new party size, or both). It "
        "changes nothing until the guest's agent confirms it.",
        ChangeArgs,
    ),
    _spec(
        "propose_cancellation",
        "Prepare the cancellation of the guest's booking. It cancels nothing until the guest's "
        "agent confirms it.",
        CancelArgs,
    ),
)
NAMES = frozenset(spec.name for spec in SPECS)


@dataclass(frozen=True)
class ToolOutcome:
    """What a tool call gave: the JSON the model reads, and what code reports from it."""

    content: str
    is_error: bool = False
    slots: tuple[Slot, ...] | None = None
    proposal: Proposal | None = None
    # A refusal that sends the guest to the merchant's own booking page.
    to_website: bool = False


class GuestTools:
    """Runs the model's tool calls for one merchant, `slug`, named `name` in the summaries."""

    def __init__(
        self,
        service: AgentService,
        tz: ZoneInfo,
        clock: Callable[[], datetime],
        *,
        slug: str,
        name: str,
    ) -> None:
        self._service = service
        self._tz = tz
        self._clock = clock
        self._slug = slug
        self._name = name

    async def run(self, call: ToolCall) -> ToolOutcome:
        try:
            match call.name:
                case "get_availability":
                    return await self._availability(
                        AvailabilityArgs.model_validate_json(call.arguments)
                    )
                case "find_my_bookings":
                    return await self._bookings(BookingsArgs.model_validate_json(call.arguments))
                case "propose_booking":
                    return await self._propose_booking(BookArgs.model_validate_json(call.arguments))
                case "propose_change":
                    return await self._propose_change(
                        ChangeArgs.model_validate_json(call.arguments)
                    )
                case "propose_cancellation":
                    return await self._propose_cancellation(
                        CancelArgs.model_validate_json(call.arguments)
                    )
                case _:
                    raise InvalidInput(f"No tool named {call.name!r}")
        except ValidationError as error:
            fields = sorted({".".join(map(str, e["loc"])) for e in error.errors()}) or ["arguments"]
            return refused(InvalidInput(f"Invalid {', '.join(fields[:5])}"))
        except AgentError as error:
            return refused(error)

    async def _availability(self, args: AvailabilityArgs) -> ToolOutcome:
        slots = await self._service.get_availability(
            self._slug, args.date, args.party_size, args.time
        )
        return ToolOutcome(
            _json({"slots": [self._slot(slot) for slot in slots]}), slots=tuple(slots)
        )

    async def _bookings(self, args: BookingsArgs) -> ToolOutcome:
        return ToolOutcome(
            _json({"bookings": [self._booking(r) for r in await self._mine(args.guest_name)]})
        )

    async def _propose_booking(self, args: BookArgs) -> ToolOutcome:
        local = args.starts_at.astimezone(self._tz)
        slots = await self._service.get_availability(
            self._slug, local.date(), args.party_size, hhmm(local, self._tz)
        )
        if not any(slot.starts_at == args.starts_at for slot in slots):
            content = {
                "error": SlotUnavailable.code,
                "message": f"{self._when(args.starts_at)} is not free for {args.party_size}",
                "other_times": [self._slot(slot) for slot in slots],
            }
            return ToolOutcome(_json(content), is_error=True, slots=tuple(slots))
        summary = (
            f"A table for {args.party_size} at {self._name} on {self._when(args.starts_at)}, "
            f"under the name {args.guest_name}."
        )
        return self._proposed("book", args, summary)

    async def _propose_change(self, args: ChangeArgs) -> ToolOutcome:
        booking = await self._booking_of(args.reservation_id, args.guest_name)
        starts_at = args.starts_at or booking.starts_at
        party_size = args.party_size or booking.party_size
        local = starts_at.astimezone(self._tz)
        # Also refuses a party above the merchant's limit.
        slots = await self._service.get_availability(
            self._slug, local.date(), party_size, hhmm(local, self._tz)
        )
        if args.starts_at is not None and not any(s.starts_at == starts_at for s in slots):
            # The booking holds its own table: a move within its slot may still fit there,
            # which the write finds out. A move further away needs a free time.
            slot_length = timedelta(
                minutes=(await self._service.get_policy(self._slug)).slot_length_min
            )
            if abs(starts_at - booking.starts_at) >= slot_length:
                content = {
                    "error": SlotUnavailable.code,
                    "message": f"{self._when(starts_at)} is not free for {party_size}",
                    "other_times": [self._slot(slot) for slot in slots],
                }
                return ToolOutcome(_json(content), is_error=True, slots=tuple(slots))
        summary = (
            f"Change booking {booking.id} at {self._name} to {party_size} guests on "
            f"{self._when(starts_at)} (now {booking.party_size} on "
            f"{self._when(booking.starts_at)}), under the name {args.guest_name}."
        )
        return self._proposed("change", args, summary)

    async def _propose_cancellation(self, args: CancelArgs) -> ToolOutcome:
        booking = await self._booking_of(args.reservation_id, args.guest_name)
        summary = (
            f"Cancel booking {booking.id} at {self._name}: {booking.party_size} guests on "
            f"{self._when(booking.starts_at)}, under the name {args.guest_name}."
        )
        return self._proposed("cancel", args, summary)

    def _proposed(
        self, action: PendingKind, args: BookArgs | ChangeArgs | CancelArgs, summary: str
    ) -> ToolOutcome:
        proposal = Proposal(
            id=f"p-{secrets.token_urlsafe(16)}",
            action=action,
            arguments=args,
            summary=summary[:MAX_PENDING_SUMMARY],
            expires_at=self._clock() + PROPOSAL_TTL,
        )
        return ToolOutcome(_json({"proposal": summary, "status": WAITING}), proposal=proposal)

    async def _mine(self, guest_name: str) -> list[Reservation]:
        """The guest's upcoming bookings here: the service looks at every merchant."""
        reservations, _ = await self._service.find_guest_reservations(guest_name)
        return [r for r in reservations if r.merchant == self._slug]

    async def _booking_of(self, reservation_id: str, guest_name: str) -> Reservation:
        for booking in await self._mine(guest_name):
            if booking.id == reservation_id:
                return booking
        raise NotFound(f"No upcoming booking {reservation_id} under that name here")

    def _when(self, moment: datetime) -> str:
        return when(moment, self._tz)

    def _slot(self, slot: Slot) -> dict[str, Any]:
        return {"starts_at": slot.starts_at.isoformat(), "local": self._when(slot.starts_at)}

    def _booking(self, booking: Reservation) -> dict[str, Any]:
        return {
            "id": booking.id,
            "starts_at": booking.starts_at.isoformat(),
            "local": self._when(booking.starts_at),
            "party_size": booking.party_size,
        }


def refused(error: AgentError) -> ToolOutcome:
    """A refusal the model words; code adds the fallback URL when it sends the guest away."""
    return ToolOutcome(
        _json({"error": error.code, "message": str(error)}),
        is_error=True,
        to_website=error.code in SENDS_TO_WEBSITE,
    )


def when(moment: datetime, tz: ZoneInfo) -> str:
    """`Monday 2 November 2026 at 20:00`, in the merchant's time zone."""
    local = moment.astimezone(tz)
    return f"{local:%A} {local.day} {local:%B %Y} at {hhmm(local, tz)}"


def _json(content: dict[str, Any]) -> str:
    return json.dumps(content, ensure_ascii=False)
