"""What the shared endpoint's booking tools and each merchant's own have in common (#23, #220).

Both MCP servers expose the same 4 booking tools: the same hints, argument types, rule for writes
and bodies (`Tools`). They differ only in where the merchant comes from (an argument, or the path)
and in the sentences that open each description. Claude reads these texts: they are a contract
(#14, #109), so a change here changes what both endpoints tell it.
"""

import textwrap
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from functools import partial
from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field, PositiveInt
from sqlalchemy.ext.asyncio import AsyncEngine

from agent import discovery
from core.config import Settings
from core.models import (
    HHMM,
    BookingDay,
    BookingTime,
    CancelOutput,
    GetAvailabilityOutput,
    GetMyReservationsOutput,
    GuestText,
    ReservationOutput,
    SearchFilters,
    SearchMerchantsOutput,
    ToolError,
    UpdateReservationInput,
)
from gateway.agent_client import AgentClient
from gateway.fallback import call_merchant, call_service

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
    """`get_availability`: up to `MAX_SLOTS` slots, as the agent found them."""
    slots = await agent.get_availability(merchant, day, party_size, time)
    return GetAvailabilityOutput(slots=tuple(slots))


# The write helpers take keywords only: their arguments are mostly strings, which a positional
# call could swap without any type error.


async def book(
    agent: AgentClient,
    *,
    merchant: str,
    starts_at: datetime,
    party_size: int,
    guest_name: str,
    idempotency_key: str,
) -> ReservationOutput:
    """`create_reservation`: the booking the agent made, or the one this key already made."""
    reservation = await agent.create_reservation(
        merchant, starts_at, party_size, guest_name, idempotency_key
    )
    return ReservationOutput(reservation=reservation)


async def update(
    agent: AgentClient,
    *,
    merchant: str,
    reservation_id: str,
    guest_name: str,
    idempotency_key: str,
    starts_at: datetime | None,
    party_size: int | None,
) -> ReservationOutput:
    """`update_reservation`, once the contract has checked the change (a no-op is invalid)."""
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
    agent: AgentClient, *, merchant: str, reservation_id: str, guest_name: str
) -> CancelOutput:
    """`cancel_reservation`, as the guest named."""
    await agent.cancel_reservation(merchant, reservation_id, guest_name)
    return CancelOutput(status="cancelled")


async def my_reservations(agent: AgentClient, guest_name: str) -> GetMyReservationsOutput:
    """`get_my_reservations`: the guest's upcoming bookings and notices."""
    reservations, notices = await agent.get_my_reservations(guest_name)
    return GetMyReservationsOutput(reservations=tuple(reservations), notices=tuple(notices))


class Tools:
    """What the user tools do once the SDK has checked their arguments; they never raise.

    A booking tool is routed to its merchant (`fallback.call_merchant`): the shared endpoint
    names it in an argument, a merchant's own endpoint in its path, with the deadline that
    endpoint's check started (#92). Writes take keywords only, as the helpers above do.
    """

    def __init__(self, agent: AgentClient, engine: AsyncEngine, settings: Settings) -> None:
        self.agent = agent
        self.engine = engine
        self.settings = settings

    async def search_merchants(
        self, query: str, near: str | None, day: date | None, filters: SearchFilters | None
    ) -> SearchMerchantsOutput | ToolError:
        """The search, once `discovery.check_search` let it through; when the service can't
        answer, the link of the merchant the query names, if any."""

        async def op() -> SearchMerchantsOutput:
            await discovery.check_search(query, filters, engine=self.engine)
            return await self.agent.search_merchants(query, near, day, filters)

        named = partial(discovery.find_named_merchant, query, engine=self.engine)
        return await call_service("search_merchants", op, settings=self.settings, named=named)

    async def get_availability(
        self,
        slug: str,
        day: date,
        party_size: int,
        time: str | None,
        *,
        deadline: float | None = None,
    ) -> GetAvailabilityOutput | ToolError:
        op = partial(availability, self.agent, slug, day, party_size, time)
        return await self._routed("get_availability", slug, op, deadline)

    async def create_reservation(
        self,
        slug: str,
        *,
        starts_at: datetime,
        party_size: int,
        guest_name: str,
        idempotency_key: str,
        deadline: float | None = None,
    ) -> ReservationOutput | ToolError:
        op = partial(
            book,
            self.agent,
            merchant=slug,
            starts_at=starts_at,
            party_size=party_size,
            guest_name=guest_name,
            idempotency_key=idempotency_key,
        )
        return await self._routed("create_reservation", slug, op, deadline)

    async def update_reservation(
        self,
        slug: str,
        *,
        reservation_id: str,
        guest_name: str,
        idempotency_key: str,
        starts_at: datetime | None,
        party_size: int | None,
        deadline: float | None = None,
    ) -> ReservationOutput | ToolError:
        op = partial(
            update,
            self.agent,
            merchant=slug,
            reservation_id=reservation_id,
            guest_name=guest_name,
            idempotency_key=idempotency_key,
            starts_at=starts_at,
            party_size=party_size,
        )
        return await self._routed("update_reservation", slug, op, deadline)

    async def cancel_reservation(
        self, slug: str, *, reservation_id: str, guest_name: str, deadline: float | None = None
    ) -> CancelOutput | ToolError:
        op = partial(
            cancel, self.agent, merchant=slug, reservation_id=reservation_id, guest_name=guest_name
        )
        return await self._routed("cancel_reservation", slug, op, deadline)

    async def get_my_reservations(self, guest_name: str) -> GetMyReservationsOutput | ToolError:
        op = partial(my_reservations, self.agent, guest_name)
        return await call_service("get_my_reservations", op, settings=self.settings)

    async def _routed[T](
        self, tool: str, slug: str, op: Callable[[], Awaitable[T]], deadline: float | None
    ) -> T | ToolError:
        find = partial(discovery.find_merchant, slug, engine=self.engine)
        return await call_merchant(slug, tool, find, op, settings=self.settings, deadline=deadline)
