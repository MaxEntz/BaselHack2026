"""The management site's booking operations (#134): a day, its slots, add, move and cancel.

Separate from the `AgentService` protocol, which serves Claude and the admin tools: these answer
the shapes of `core.models` for the `/merchants/{slug}` routes. Writes go through the same
machinery as guests' (`agent.service`): the table's row lock, one SAVEPOINT per candidate table,
the exclusion constraint as the guarantee. A move is marked as the merchant's (`as_merchant`),
so it sends no live event: the page that made it already shows it.
"""

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, Row, func, select, update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from agent import activity, admin, dashboard
from agent.service import PgAgentService, _during, _utc
from agent.writes import attempt, candidate_tables, check_start
from core.availability import BusyInterval, TableRow, bookable_starts, pick_table
from core.db import (
    as_merchant,
    blocked_slots_t,
    merchant_conn,
    merchants_t,
    policy_t,
    reservations_t,
    tables_t,
)
from core.errors import InvalidInput, NotFound, SlotUnavailable
from core.models import (
    Dashboard,
    DaySlot,
    DaySlots,
    ManagedReservation,
    MerchantBookingInput,
    MerchantCancelOutput,
    MoveBookingInput,
    ReservationDay,
    SearchOutput,
    Service,
    TableSpec,
)

log = logging.getLogger(__name__)

SEARCH_LIMIT = 8


def _fold(text: str) -> str:
    """`same_guest`'s comparison (agent/service.py), for substrings."""
    return " ".join(text.split()).casefold()


_start_of = func.lower(reservations_t.c.during)
_MANAGED_COLUMNS = (
    reservations_t.c.id,
    _start_of.label("starts_at"),
    reservations_t.c.guest_name,
    reservations_t.c.party_size,
    tables_t.c.label,
    reservations_t.c.via,
    reservations_t.c.status,
    reservations_t.c.cancelled_by,
    reservations_t.c.created_at,
)


class _Plan:
    """The merchant's policy and floor plan, as one operation reads them."""

    def __init__(self, policy: Row[Any], tables: list[TableRow]) -> None:
        self.max_party_size: int = policy.max_party_size
        self.slot_length = timedelta(minutes=policy.slot_length_min)
        self.services = sorted(
            (Service.model_validate(s) for s in policy.services), key=lambda s: s.start
        )
        self.tables = tables

    @property
    def largest_group(self) -> int:
        return min(self.max_party_size, max((t.seats for t in self.tables), default=0))


class ManagementService:
    """`clock` returns the current time; `agent` books new reservations (`via="merchant"`)."""

    def __init__(
        self,
        engine: AsyncEngine,
        agent: PgAgentService,
        *,
        tz: ZoneInfo,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.engine = engine
        self.agent = agent
        self.tz = tz
        self.clock = clock or (lambda: datetime.now(tz))

    async def day(self, slug: str, day: date) -> ReservationDay:
        start, end = self._bounds(day)
        async with merchant_conn(self.engine, slug) as conn:
            plan = await self._plan(conn, slug)
            blocked = await conn.scalars(
                select(blocked_slots_t.c.starts_at)
                .where(blocked_slots_t.c.starts_at >= start, blocked_slots_t.c.starts_at < end)
                .order_by(blocked_slots_t.c.starts_at)
            )
            bookings = await self._managed(conn, _start_of >= start, _start_of < end)
        return ReservationDay(
            date=day,
            services=tuple(plan.services),
            starts=tuple(self._hhmm(s) for s in bookable_starts(plan.services, day, self.tz)),
            slot_length_min=int(plan.slot_length.total_seconds() // 60),
            largest_group=plan.largest_group,
            blocked=tuple(self._hhmm(moment) for moment in blocked),
            reservations=tuple(bookings),
        )

    async def slots(
        self, slug: str, day: date, party_size: int, ignore: str | None = None
    ) -> DaySlots:
        """Every start of `day` that hasn't passed, with the table a booking would get.

        With `ignore` (a move), the table the move would give: its own first among equals, and
        no party limit for the booking's own party, which the move doesn't apply either.
        """
        start, end = self._bounds(day)
        left_out = ignore.strip().upper() if ignore else None
        async with merchant_conn(self.engine, slug) as conn:
            plan = await self._plan(conn, slug)
            busy_query = select(
                reservations_t.c.id,
                reservations_t.c.table_id,
                reservations_t.c.during,
                reservations_t.c.party_size,
            ).where(
                reservations_t.c.status == "confirmed",
                reservations_t.c.during.overlaps(Range(start, _utc(end) + plan.slot_length)),
            )
            rows = (await conn.execute(busy_query)).all()
            busy = [
                BusyInterval(row.table_id, row.during.lower, row.during.upper)
                for row in rows
                if row.id != left_out
            ]
            moving = next((row for row in rows if row.id == left_out), None)
            blocked = set(
                await conn.scalars(
                    select(blocked_slots_t.c.starts_at).where(
                        blocked_slots_t.c.starts_at >= start, blocked_slots_t.c.starts_at < end
                    )
                )
            )
        now = _utc(self.clock())
        own_party = moving is not None and moving.party_size == party_size
        fits = party_size <= plan.max_party_size or own_party
        own_table = next((t for t in plan.tables if moving and t.id == moving.table_id), None)
        slots = []
        for moment in bookable_starts(plan.services, day, self.tz):
            if _utc(moment) <= now:
                continue
            table = pick_table(plan.tables, party_size, moment, plan.slot_length, busy)
            if table and own_table and own_party and own_table.seats == table.seats:
                free = pick_table([own_table], party_size, moment, plan.slot_length, busy)
                table = free or table  # the move keeps its own table among equals
            slots.append(
                DaySlot(
                    starts_at=moment,
                    time=self._hhmm(moment),
                    # In UTC: a slot lasts its minutes on a clock-change night too (#80).
                    until=self._hhmm(_utc(moment) + plan.slot_length),
                    table=TableSpec(label=table.label, seats=table.seats)
                    if table and fits
                    else None,
                    blocked=any(_utc(b) == _utc(moment) for b in blocked),
                )
            )
        return DaySlots(date=day, party_size=party_size, slots=tuple(slots))

    async def add(self, slug: str, body: MerchantBookingInput) -> ManagedReservation:
        booked = await self.agent.create_reservation(
            slug,
            body.starts_at,
            body.party_size,
            body.guest_name,
            body.idempotency_key,
            via="merchant",
        )
        return await self.reservation(slug, booked.id)

    async def move(
        self, slug: str, reservation_id: str, body: MoveBookingInput
    ) -> ManagedReservation:
        """Another start the same day, for a booking that hasn't started; the table is picked
        again, smallest fit first, the booking's own table first among equals."""
        wanted = reservation_id.strip().upper()
        async with merchant_conn(self.engine, slug) as conn:
            await as_merchant(conn)
            plan = await self._plan(conn, slug)
            current = (
                await conn.execute(
                    select(reservations_t)
                    .where(reservations_t.c.id == wanted)
                    .with_for_update(of=reservations_t)
                )
            ).one_or_none()
            if current is None:
                raise NotFound(f"No booking {reservation_id!r}")
            # The guests' update keys and the merchant's share one table (ADR-0013).
            fresh = await self.agent._claim_update_key(
                conn, slug, body.idempotency_key, current.id, body.starts_at, None
            )
            if not fresh:
                return (await self._managed(conn, reservations_t.c.id == current.id))[0]
            self._check_movable(current, body.starts_at)
            check_start(plan.services, body.starts_at, now=self.clock(), tz=self.tz)
            if await conn.scalar(
                select(blocked_slots_t.c.starts_at).where(
                    blocked_slots_t.c.starts_at == body.starts_at
                )
            ):
                raise SlotUnavailable(f"{self._hhmm(body.starts_at)} is blocked")
            during = _during(body.starts_at, plan.slot_length)
            # Smallest fit first (AGENTS.md §6); among equals, the booking keeps its own table.
            tables = sorted(
                candidate_tables(plan.tables, current.party_size),
                key=lambda t: (t.seats, t.id != current.table_id, t.label),
            )
            for table in tables:
                outcome = await attempt(
                    conn,
                    table.id,
                    current.party_size,
                    update(reservations_t)
                    .where(reservations_t.c.id == current.id)
                    .values(table_id=table.id, during=during),
                )
                if outcome == "done":
                    if current.via != "merchant":  # a guest made it: tell them
                        await admin.notify_move(
                            conn, slug, current.id, current.guest_name, body.starts_at, self.tz
                        )
                    await activity.record_booking(
                        conn,
                        slug,
                        "moved",
                        "merchant",
                        current.id,
                        current.guest_name,
                        current.party_size,
                        body.starts_at,
                    )
                    log.info(
                        "reservation moved by merchant",
                        extra={"merchant": slug, "reservation": current.id},
                    )
                    return (await self._managed(conn, reservations_t.c.id == current.id))[0]
            # Inside the transaction: it rolls back, and the key claimed above with it.
            raise SlotUnavailable(f"No table left at {self._hhmm(body.starts_at)}")

    async def cancel(self, slug: str, reservation_id: str) -> MerchantCancelOutput:
        done = await self.agent.cancel_by_merchant(slug, reservation_id)
        return MerchantCancelOutput(
            reservation=await self.reservation(slug, done.reservation.id), outcome=done.outcome
        )

    async def dashboard(self, slug: str, day: date | None = None) -> Dashboard:
        """Home for the merchant's today by the server's clock; another day is refused."""
        now = self.clock()
        today = now.astimezone(self.tz).date()
        if day is not None and day != today:
            raise InvalidInput(f"The dashboard is for today ({today}), not {day}")
        async with merchant_conn(self.engine, slug) as conn:
            plan = await self._plan(conn, slug)
            history, next_days = await dashboard.days(conn, today, self.tz)
            room = await dashboard.room(
                conn,
                services=plan.services,
                tables=plan.tables,
                slot_length=plan.slot_length,
                largest_group=plan.largest_group,
                max_party_size=plan.max_party_size,
                now=now,
                tz=self.tz,
            )
            return Dashboard(
                date=today,
                history=tuple(history),
                next_days=tuple(next_days),
                channels=tuple(await dashboard.channels(conn, today, self.tz)),
                room=room,
                activity=tuple(await dashboard.recent(conn, slug, today, self.tz)),
            )

    async def search(self, slug: str, q: str) -> SearchOutput:
        """Up to 8 bookings of the next 7 days by guest name or reference, cancelled included;
        names compare as `same_guest` does (case folded, runs of spaces as one)."""
        wanted = _fold(q)
        if not wanted:
            return SearchOutput(reservations=())
        today = self.clock().astimezone(self.tz).date()
        start, _ = self._bounds(today)
        _, end = self._bounds(today + timedelta(days=dashboard.NEXT_DAYS - 1))
        async with merchant_conn(self.engine, slug) as conn:
            await self._plan(conn, slug)
            ahead = await self._managed(conn, _start_of >= start, _start_of < end)
        found = [b for b in ahead if wanted in _fold(b.guest_name) or wanted in _fold(b.id)]
        return SearchOutput(reservations=tuple(found[:SEARCH_LIMIT]))

    async def reservation(self, slug: str, reservation_id: str) -> ManagedReservation:
        async with merchant_conn(self.engine, slug) as conn:
            await self._plan(conn, slug)
            found = await self._managed(conn, reservations_t.c.id == reservation_id)
        if not found:
            raise NotFound(f"No booking {reservation_id!r}")
        return found[0]

    # Helpers

    def _check_movable(self, current: Row[Any], starts_at: datetime) -> None:
        if current.status != "confirmed":
            raise SlotUnavailable(f"Booking {current.id} is cancelled")
        if _utc(current.during.lower) <= _utc(self.clock()):
            raise SlotUnavailable(f"Booking {current.id} has already started")
        if starts_at.astimezone(self.tz).date() != current.during.lower.astimezone(self.tz).date():
            raise InvalidInput("A booking moves within its day")

    async def _plan(self, conn: AsyncConnection, slug: str) -> _Plan:
        """The merchant's policy and tables; a `building` merchant is its owner's too."""
        if await conn.scalar(select(merchants_t.c.slug).where(merchants_t.c.slug == slug)) is None:
            raise NotFound(f"Unknown merchant: {slug!r}")
        policy = (await conn.execute(select(policy_t))).one()
        tables = [
            TableRow(id=row.id, label=row.label, seats=row.seats)
            for row in await conn.execute(select(tables_t.c.id, tables_t.c.label, tables_t.c.seats))
        ]
        return _Plan(policy, tables)

    async def _managed(
        self, conn: AsyncConnection, *conditions: ColumnElement[bool]
    ) -> list[ManagedReservation]:
        query = (
            select(*_MANAGED_COLUMNS)
            .join(tables_t, tables_t.c.id == reservations_t.c.table_id)
            .where(*conditions)
            .order_by(_start_of, reservations_t.c.id)
        )
        return [
            ManagedReservation(
                id=row.id,
                date=row.starts_at.astimezone(self.tz).date(),
                time=self._hhmm(row.starts_at),
                guest_name=row.guest_name,
                party_size=row.party_size,
                table_label=row.label,
                via=row.via,
                status=row.status,
                cancelled_by=row.cancelled_by,
                created_at=row.created_at.astimezone(self.tz),
            )
            for row in await conn.execute(query)
        ]

    def _bounds(self, day: date) -> tuple[datetime, datetime]:
        start = datetime.combine(day, datetime.min.time(), tzinfo=self.tz)
        end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=self.tz)
        return start, end

    def _hhmm(self, moment: datetime) -> str:
        return f"{moment.astimezone(self.tz):%H:%M}"
