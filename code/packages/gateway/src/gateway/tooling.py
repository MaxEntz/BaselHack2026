"""What the shared endpoint's booking tools and each merchant's own have in common (#23, #220).

Both MCP servers expose the same 4 booking tools, or `talk_to_merchant` instead (`GATEWAY_TALK`,
#251): the same hints, argument types, rule for writes and bodies (`Tools`). They differ only in
where the merchant comes from (an argument, or the path) and in the sentences that open each
description. Claude reads these texts: they are a contract (#14, #109), so a change here changes
what both endpoints tell it.
"""

import textwrap
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from functools import partial
from typing import Annotated
from uuid import UUID

from mcp.types import ToolAnnotations
from pydantic import AfterValidator, Field, PositiveInt
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
    OpaqueId,
    ReservationOutput,
    SearchFilters,
    SearchMerchantsOutput,
    TalkInput,
    TalkMessage,
    TalkOutput,
    ToolError,
    UpdateReservationInput,
)
from gateway.agent_client import AgentClient
from gateway.fallback import TALK_TOOL, call_merchant, call_service

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
IDEMPOTENT_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
)
# A conversation can end in a cancellation, once the user said yes; a key makes a resend harmless.
CONVERSATION = ToolAnnotations(
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
Message = Annotated[
    TalkMessage,
    Field(
        description=(
            "What the user wants, in natural language, as they would say it to the restaurant, "
            "with their known preferences."
        ),
    ),
]


# A real UUID, not free text: every Claude user reaches the node under the gateway's one agent id,
# and the node replays a key's stored answer. Two guests sending the same first message with the
# same naive key ("m-1") would share a conversation and its proposal (#251, review of #264).
def _not_nil(key: UUID) -> UUID:
    # The nil UUID is the example a model is likeliest to copy instead of generating one.
    if key.int == 0:
        raise ValueError("generate a random UUID, not the nil one")
    return key


TalkKey = Annotated[
    UUID,
    AfterValidator(_not_nil),
    Field(
        description=(
            "A fresh random UUID you generate per message; reuse it only to resend the same "
            "message."
        )
    ),
]
Conversation = Annotated[
    OpaqueId | None,
    Field(description="The `conversation` of the agent's last answer; omit it to start one."),
]
Confirm = Annotated[
    OpaqueId | None,
    Field(
        description=(
            "The `pending.id` the user said an explicit yes to, with that answer's `conversation`."
        )
    ),
]

# The rule for writes in the server instructions (#14, #109): a yes to a summary, never one given
# before it, and never a write in the reply that made the summary. `{chosen}` is what the user
# picks from the results of `{calls}`.
NOT_A_YES = (
    "Choosing {chosen} is not a yes, even when the request looks complete. Nor is a "
    'confirmation given before the summary ("I confirm in advance", "book directly"): show the '
    "summary anyway."
)
CONFIRM_RULE = (
    "Before create_reservation, update_reservation or cancel_reservation, summarize the action "
    "and stop: call the tool only once the user's reply to that summary is an explicit yes. "
    f"{NOT_A_YES} So never call a write tool in the same reply as the {{calls}} call your "
    "summary is based on. After the user's yes, checking availability again first is fine; if "
    "that check no longer offers the confirmed time, don't book another one: show the new times "
    "and ask again."
)
# The same rule when the merchant's agent writes (#251): the agent proposes, as `pending`, and
# only a `confirm` sent after the user's yes to its summary lets it write.
TALK_CONFIRM_RULE = (
    "When talk_to_merchant returns `pending`, show the user its `summary`, ask them to confirm, "
    "and stop: send its `id` as `confirm` only once the user's reply to that summary is an "
    f"explicit yes. {NOT_A_YES} So never send `confirm` in the same reply as the "
    "talk_to_merchant call that returned the `pending`. If the agent then answers that the "
    "confirmed time is gone, don't confirm another one: relay its new times and ask again."
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


# What `talk_to_merchant` says on both endpoints, after each one's opening sentence.
TALK_RULES = (
    "Write `message` as the user would say it to the restaurant, with what they want and their "
    "known preferences (dietary needs, terrace, kid-friendly, budget). Relay `reply` to the user, "
    "and only propose times the agent offered. To go on with the same agent, pass back the "
    "`conversation` of its last answer. When the result contains `pending`, show the user its "
    "`summary`, ask them to confirm, and stop. Send `confirm` (that `pending.id`) only once their "
    "reply to that summary is an explicit yes, even if they confirmed in advance, and never in "
    "the same reply as the call that returned the `pending`. Generate a fresh random UUID as "
    "`idempotency_key` per message and reuse it only to resend the same message. "
    f"{GIVE_FALLBACK}"
)


def bullet(text: str) -> str:
    """`text` as a bullet of the server instructions."""
    return textwrap.fill(
        text, width=INSTRUCTIONS_WIDTH, initial_indent="- ", subsequent_indent="  "
    )


def confirm_rule(chosen: str, calls: str) -> str:
    """`CONFIRM_RULE` as a bullet of the server instructions."""
    return bullet(CONFIRM_RULE.format(chosen=chosen, calls=calls))


def talk_confirm_rule(chosen: str) -> str:
    """`TALK_CONFIRM_RULE` as a bullet of the server instructions."""
    return bullet(TALK_CONFIRM_RULE.format(chosen=chosen))


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


async def talk(
    agent: AgentClient,
    *,
    merchant: str,
    message: str,
    idempotency_key: UUID,
    conversation: str | None,
    confirm: str | None,
) -> TalkOutput:
    """`talk_to_merchant`: the agent's answer, once the contract has checked the message. The key
    travels in its canonical form, so that one UUID written two ways stays one key."""
    turn = TalkInput(
        merchant=merchant,
        message=message,
        idempotency_key=str(idempotency_key),
        conversation=conversation,
        confirm=confirm,
    )
    return await agent.talk(turn)


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

    async def talk_to_merchant(
        self,
        slug: str,
        *,
        message: str,
        idempotency_key: UUID,
        conversation: str | None,
        confirm: str | None,
        deadline: float | None = None,
    ) -> TalkOutput | ToolError:
        """A turn of the conversation; only one carrying a `confirm` may have written."""
        op = partial(
            talk,
            self.agent,
            merchant=slug,
            message=message,
            idempotency_key=idempotency_key,
            conversation=conversation,
            confirm=confirm,
        )
        return await self._routed(TALK_TOOL, slug, op, deadline, writes=confirm is not None)

    async def _routed[T](
        self,
        tool: str,
        slug: str,
        op: Callable[[], Awaitable[T]],
        deadline: float | None,
        *,
        writes: bool | None = None,
    ) -> T | ToolError:
        find = partial(discovery.find_merchant, slug, engine=self.engine)
        return await call_merchant(
            slug, tool, find, op, settings=self.settings, deadline=deadline, writes=writes
        )
