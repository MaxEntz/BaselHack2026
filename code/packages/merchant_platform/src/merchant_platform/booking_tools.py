"""The admin tools on the merchant's agent (AGENTS.md §8.2): its bookings, figures, blocked
times, policy and floor plan, and what an AI agent gets from it (#152)."""

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from agent.service import AgentService
from agent.times import hhmm
from agent.words import short_day
from builder.words import joined
from core.errors import AgentUnavailable, InvalidInput, PartyTooLarge
from core.models import (
    AdminCancelInput,
    GetStatsInput,
    ListReservationsInput,
    MerchantStatus,
    PolicyCard,
    ReservationsCard,
    ReservationsCardData,
    SendTestBookingInput,
    SlotInput,
    StatsCard,
    StatsCardData,
    TryRequestInput,
    UpdatePolicyInput,
    UpdateRulesInput,
    UpdateTablesInput,
)
from merchant_platform.tool_specs import (
    CANCELLED,
    HIDDEN,
    NOT_AVAILABLE,
    POLICY_SAVED,
    TEST_HIDDEN,
    Outcome,
    confirmation,
    diff,
)
from merchant_platform.trial import TEST_GUEST, TEST_PARTY, first_free_slot

# The merchant's registry status, None for an unknown merchant.
StatusLookup = Callable[[str], Awaitable[MerchantStatus | None]]


class BookingTools:
    """One method per tool, for the merchant `slug`; `agent_unavailable` without an agent."""

    def __init__(
        self,
        agent: AgentService | None,
        tz: ZoneInfo,
        today: Callable[[], date],
        status: StatusLookup,
    ) -> None:
        self._service = agent
        self._tz = tz
        self._today = today
        self._status = status

    @property
    def _agent(self) -> AgentService:
        if self._service is None:
            raise AgentUnavailable(NOT_AVAILABLE)
        return self._service

    async def list_reservations(self, slug: str, arguments: ListReservationsInput) -> Outcome:
        agent, day = self._agent, arguments.date
        data = ReservationsCardData(
            date=day, reservations=tuple(await agent.list_reservations(slug, day))
        )
        return Outcome(
            {"shown_to_merchant": "bookings table", **data.model_dump(mode="json")},
            ReservationsCard(data=data),
        )

    async def get_stats(self, slug: str, arguments: GetStatsInput) -> Outcome:
        agent, day = self._agent, arguments.date
        stats = await agent.get_stats(slug, day)
        data = StatsCardData(date=day, **stats.model_dump())
        return Outcome(
            {"shown_to_merchant": "figures", **data.model_dump(mode="json")}, StatsCard(data=data)
        )

    async def block_slot(self, slug: str, arguments: SlotInput) -> Outcome:
        agent, starts_at = self._agent, arguments.starts_at
        self._check_offset(starts_at)
        await agent.block_slot(slug, starts_at)
        return confirmation(f"{self._when(starts_at)} blocked: no new bookings.")

    async def unblock_slot(self, slug: str, arguments: SlotInput) -> Outcome:
        agent, starts_at = self._agent, arguments.starts_at
        self._check_offset(starts_at)
        await agent.unblock_slot(slug, starts_at)
        return confirmation(f"{self._when(starts_at)} open for bookings again.")

    async def update_policy(self, slug: str, arguments: UpdatePolicyInput) -> Outcome:
        policy = await self._agent.update_policy(slug, arguments.max_party_size, arguments.services)
        return Outcome(
            {"shown_to_merchant": "policy", **policy.model_dump(mode="json")},
            PolicyCard(data=policy),
        )

    async def update_tables(self, slug: str, arguments: UpdateTablesInput) -> Outcome:
        saved = await self._agent.update_tables(slug, arguments.tables)
        seats = sum(table.seats for table in saved)
        return confirmation(f"Floor plan updated: {len(saved)} tables · {seats} seats.")

    async def update_rules(self, slug: str, arguments: UpdateRulesInput) -> Outcome:
        agent = self._agent
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
            diff("Cancelling", before.cancellation_policy, after.cancellation_policy or "None"),
        )

    async def cancel(self, slug: str, arguments: AdminCancelInput) -> Outcome:
        done = await self._agent.cancel_by_merchant(slug, arguments.reservation_id)
        return confirmation(CANCELLED[done.outcome].format(id=done.reservation.id))

    async def try_request(self, slug: str, arguments: TryRequestInput) -> Outcome:
        """What an AI agent gets when it asks for a table: the times, or the refusal."""
        agent, n = self._agent, arguments.party_size
        if await self._status(slug) == "building":
            return confirmation(HIDDEN)
        day = arguments.date or self._today()
        try:
            slots = await agent.get_availability(slug, day, n, arguments.time)
        except PartyTooLarge:
            return confirmation(
                f"An agent asking for {n} is told it's too many, with the link to your booking "
                "page, and the guest goes there."
            )
        if not slots:
            return confirmation(
                f"An agent asking for {n} on {short_day(day)} gets no time: every table that fits "
                "is taken or blocked."
            )
        times = [self._local_time(slot.starts_at) for slot in slots]
        count = "1 time" if len(times) == 1 else f"{len(times)} times"
        return confirmation(f"An agent asking for {n} gets {count}: {joined(times)}.")

    async def send_test_booking(self, slug: str, _: SendTestBookingInput) -> Outcome:
        """A real booking for the test guest, as an agent would make it."""
        agent = self._agent
        if await self._status(slug) == "building":
            return confirmation(TEST_HIDDEN)
        slot = await first_free_slot(agent, slug, self._today())
        if slot is None:
            return confirmation(
                f"No table for {TEST_PARTY} is free today or tomorrow to try it on."
            )
        booked = await agent.create_reservation(
            slug, slot.starts_at, TEST_PARTY, TEST_GUEST, f"studio-test-{uuid4()}", via="claude"
        )
        day = short_day(slot.starts_at.astimezone(self._tz).date())
        return confirmation(
            f"Sent: {booked.id}, {day} at {self._local_time(slot.starts_at)} for {TEST_PARTY}, "
            "booked as an agent would. It's in your bookings: cancel it when you're done."
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
            f"UTC{meant:%:z} on {meant:%Y-%m-%d}. For {hhmm(meant)} local time, send "
            f"{meant.isoformat()}."
        )

    def _local_time(self, at: datetime) -> str:
        return hhmm(at, self._tz)

    def _when(self, starts_at: datetime) -> str:
        local = starts_at.astimezone(self._tz)
        day = local.date()
        if day == self._today():
            return f"{hhmm(local)} tonight" if local.hour >= 17 else f"{hhmm(local)} today"
        return f"{hhmm(local)} on {short_day(day)}"
