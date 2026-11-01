"""What the shared endpoint's booking tools and each merchant's own have in common (#23, #220).

Both MCP servers expose the same 4 booking tools: the same hints, argument types, rule for writes
and bodies. They differ only in where the merchant comes from (an argument, or the path) and in
the sentences that open each description. Claude reads these texts: they are a contract (#14,
#109), so a change here changes what both endpoints tell it.
"""

import textwrap
from datetime import date, datetime
from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field, PositiveInt

from core.models import (
    HHMM,
    BookingDay,
    BookingTime,
    CancelOutput,
    GetAvailabilityOutput,
    GuestText,
    ReservationOutput,
    UpdateReservationInput,
)
from gateway.agent_client import AgentClient

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
IDEMPOTENT_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
)

Day = Annotated[BookingDay, Field(description="Requested day, YYYY-MM-DD.")]
Time = Annotated[HHMM | None, Field(description="Requested time (HH:MM), 24-hour clock.")]
PartySize = Annotated[PositiveInt, Field(description="Number of guests.")]
StartsAt = Annotated[
    BookingTime,
    Field(description="Start time with its UTC offset, exactly as returned by get_availability."),
]
GuestName = Annotated[
    GuestText, Field(description="Guest's full name, the same one for the whole booking.")
]
IdempotencyKey = Annotated[
    GuestText, Field(description="UUID you generate; reuse it only to retry the same action.")
]
ReservationId = Annotated[GuestText, Field(description="Booking reference, e.g. R-001.")]
NewStartsAt = Annotated[
    BookingTime | None,
    Field(description="New start time with its UTC offset, from get_availability."),
]
NewPartySize = Annotated[PositiveInt | None, Field(description="New number of guests.")]

# The rule for writes in the server instructions (#14, #109): a yes to a summary, never one given
# before it, and never a write in the reply that made the summary. `{chosen}` is what the user
# picks from the results of `{calls}`.
CONFIRM_RULE = (
    "Before create_reservation, update_reservation or cancel_reservation, summarize the action "
    "and stop: call the tool only once the user's reply to that summary is an explicit yes. "
    "Choosing {chosen} is not a yes, even when the request looks complete. Nor is a "
    'confirmation given before the summary ("I confirm in advance", "book directly"): show the '
    "summary anyway. So never call a write tool in the same reply as the {calls} call your "
    "summary is based on. After the user's yes, checking availability again first is fine; if "
    "that check no longer offers the confirmed time, don't book another one: show the new times "
    "and ask again."
)
# Instructions are bullets of at most this many characters a line.
INSTRUCTIONS_WIDTH = 98

GIVE_FALLBACK = "If the result contains `fallback_url`, give it to the user."
# How `create_reservation` ends on both endpoints, after each one's own summary rule.
BOOK_AFTER_YES = (
    "After the yes, you may check availability again before booking; if that check no longer "
    "offers the confirmed time, don't book another one: show the new times and ask again. "
    "Generate a fresh `idempotency_key` (UUID) per confirmed booking and reuse it if you retry "
    f"the same booking. Ask the guest's name if unknown. {GIVE_FALLBACK}"
)
UPDATE_RULE = (
    "First show the change, ask the user to confirm, and stop; call this tool only once they "
    "reply yes to that summary, even if they confirmed in advance. Requires the guest name used "
    "at booking. Generate a fresh `idempotency_key` (UUID) per confirmed change and reuse it if "
    f"you retry the same change. {GIVE_FALLBACK}"
)
CANCEL_RULE = (
    "First ask the user to confirm, and stop; call this tool only once they reply yes to that "
    "question, even if they confirmed in advance. Requires the guest name used at booking. "
    f"{GIVE_FALLBACK}"
)


def confirm_rule(chosen: str, calls: str) -> str:
    """`CONFIRM_RULE` as a bullet of the server instructions."""
    return textwrap.fill(
        CONFIRM_RULE.format(chosen=chosen, calls=calls),
        width=INSTRUCTIONS_WIDTH,
        initial_indent="- ",
        subsequent_indent="  ",
    )


async def availability(
    agent: AgentClient, merchant: str, day: date, party_size: int, time: str | None
) -> GetAvailabilityOutput:
    slots = await agent.get_availability(merchant, day, party_size, time)
    return GetAvailabilityOutput(slots=tuple(slots))


async def book(
    agent: AgentClient,
    merchant: str,
    starts_at: datetime,
    party_size: int,
    guest_name: str,
    idempotency_key: str,
) -> ReservationOutput:
    reservation = await agent.create_reservation(
        merchant, starts_at, party_size, guest_name, idempotency_key
    )
    return ReservationOutput(reservation=reservation)


async def update(
    agent: AgentClient,
    merchant: str,
    reservation_id: str,
    guest_name: str,
    idempotency_key: str,
    starts_at: datetime | None,
    party_size: int | None,
) -> ReservationOutput:
    """The contract checks the change before the agent sees it: one that changes nothing is
    `invalid_input`."""
    change = UpdateReservationInput(
        merchant=merchant,
        reservation_id=reservation_id,
        guest_name=guest_name,
        idempotency_key=idempotency_key,
        starts_at=starts_at,
        party_size=party_size,
    )
    reservation = await agent.update_reservation(
        change.merchant,
        change.reservation_id,
        change.guest_name,
        change.idempotency_key,
        change.starts_at,
        change.party_size,
    )
    return ReservationOutput(reservation=reservation)


async def cancel(
    agent: AgentClient, merchant: str, reservation_id: str, guest_name: str
) -> CancelOutput:
    await agent.cancel_reservation(merchant, reservation_id, guest_name)
    return CancelOutput(status="cancelled")
