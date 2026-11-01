"""The management site's booking operations (#134): a day, its slots, add, move and cancel,
and Home's dashboard and the search box, which read the same bookings (`ManagementService`).

Separate from the `AgentService` protocol, which serves Claude and the admin tools: these answer
the shapes of `core.models` for the `/merchants/{slug}` routes. Writes go through the same
machinery as guests' (`agent.writes`): the table's row lock, one SAVEPOINT per candidate table,
the exclusion constraint as the guarantee. A move is marked as the merchant's (`as_merchant`),
so it sends no live event: the page that made it already shows it.
"""

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from agent import activity, dashboard, notices
from agent.guests import guest_key
from agent.merchant import load_merchant
from agent.service import PgAgentService
from agent.times import day_bounds, during, hhmm, utc
from agent.writes import (
    candidate_tables,
    check_new_start,
    check_not_started,
    claim_update_key,
    first_free,
    moved_to,
)
from core.availability import bookable_starts, pick_table
from core.db import (
    AnyRow,
    as_merchant,
    blocked_between,
    busy_between,
    merchant_conn,
    reservation_start,
    reservations_t,
    tables_t,
)
from core.errors import InvalidInput, NotFound, SlotUnavailable
from core.models import (
    SEARCH_LIMIT,
    Dashboard,
    DaySlot,
    DaySlots,
    ManagedReservation,
    MerchantBookingInput,
    MerchantCancelOutput,
    MoveBookingInput,
    ReservationDay,
    SearchOutput,
    TableSpec,
)

log = logging.getLogger(__name__)

_MANAGED_COLUMNS = (
    reservations_t.c.id,
    reservation_start.label("starts_at"),
    reservations_t.c.guest_name,
    reservations_t.c.party_size,
    tables_t.c.label,
    reservations_t.c.via,
    reservations_t.c.status,
    reservations_t.c.cancelled_by,
    reservations_t.c.created_at,
)


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
        start, end = day_bounds(day, self.tz)
        async with merchant_conn(self.engine, slug) as conn:
            merchant = await load_merchant(conn, slug, merchant_side=True)
            blocked = await blocked_between(conn, start, end)
            bookings = await self._managed(
                conn, reservation_start >= start, reservation_start < end
            )
        return ReservationDay(
            date=day,
            services=merchant.services,
            starts=tuple(
                hhmm(s, self.tz) for s in bookable_starts(merchant.services, day, self.tz)
            ),
            slot_length_min=merchant.slot_length_min,
            largest_group=merchant.largest_group,
            blocked=tuple(hhmm(moment, self.tz) for moment in blocked),
            reservations=tuple(bookings),
        )

    async def slots(
        self, slug: str, day: date, party_size: int, ignore: str | None = None
    ) -> DaySlots:
        """Every start of `day` that hasn't passed, with the table a booking would get.

        With `ignore` (a move), the table the move would give: its own first among equals, and
        no party limit for the booking's own party, which the move doesn't apply either.
        """
        start, end = day_bounds(day, self.tz)
        left_out = ignore.strip().upper() if ignore else None
        async with merchant_conn(self.engine, slug) as conn:
            merchant = await load_merchant(conn, slug, merchant_side=True)
            held = await busy_between(conn, start, utc(end) + merchant.slot_length)
            busy = [booked for booked in held if booked.reservation_id != left_out]
            moving = next((booked for booked in held if booked.reservation_id == left_out), None)
            blocked = set(await blocked_between(conn, start, end))
        now = utc(self.clock())
        own_party = moving is not None and moving.party_size == party_size
        fits = party_size <= merchant.max_party_size or own_party
        own_table = next((t for t in merchant.tables if moving and t.id == moving.table_id), None)
        slots = []
        for moment in bookable_starts(merchant.services, day, self.tz):
            if utc(moment) <= now:
                continue
            table = pick_table(merchant.tables, party_size, moment, merchant.slot_length, busy)
            if table and own_table and own_party and own_table.seats == table.seats:
                free = pick_table([own_table], party_size, moment, merchant.slot_length, busy)
                table = free or table  # the move keeps its own table among equals
            slots.append(
                DaySlot(
                    starts_at=moment,
                    time=hhmm(moment, self.tz),
                    # In UTC: a slot lasts its minutes on a clock-change night too (#80).
                    until=hhmm(utc(moment) + merchant.slot_length, self.tz),
                    table=TableSpec(label=table.label, seats=table.seats)
                    if table and fits
                    else None,
                    blocked=any(utc(b) == utc(moment) for b in blocked),
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
        async with merchant_conn(self.engine, slug) as conn:
            await as_merchant(conn)
            merchant = await load_merchant(conn, slug, merchant_side=True)
            current = await self._locked(conn, reservation_id)
            # The guests' update keys and the merchant's share one table (ADR-0013).
            if not await claim_update_key(
                conn, slug, body.idempotency_key, current.id, body.starts_at, None
            ):
                return await self._current(conn, current.id)
            self._check_movable(current, body.starts_at)
            await check_new_start(
                conn, merchant.services, body.starts_at, now=self.clock(), tz=self.tz
            )
            span = during(body.starts_at, merchant.slot_length)
            # Smallest fit first (AGENTS.md §6); among equals, the booking keeps its own table.
            tables = sorted(
                candidate_tables(merchant.tables, current.party_size),
                key=lambda t: (t.seats, t.id != current.table_id, t.label),
            )
            table = await first_free(
                conn,
                tables,
                current.party_size,
                moved_to(current.id, span),
            )
            if table is None:
                # Inside the transaction: it rolls back, and the key claimed above with it.
                raise SlotUnavailable(f"No table left at {hhmm(body.starts_at, self.tz)}")
            # Moved to where it was: nothing to tell or to log (#135).
            if utc(body.starts_at) != utc(current.during.lower):
                await self._tell_move(conn, slug, current, body.starts_at)
            return await self._current(conn, current.id)

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
            merchant = await load_merchant(conn, slug, merchant_side=True)
            history, next_days = await dashboard.days(conn, today, self.tz)
            room = await dashboard.room(conn, merchant, now=now, tz=self.tz)
            return Dashboard(
                date=today,
                history=tuple(history),
                next_days=tuple(next_days),
                channels=tuple(await dashboard.channels(conn, today, self.tz)),
                room=room,
                activity=tuple(await dashboard.recent(conn, slug, today, self.tz)),
            )

    async def search(self, slug: str, q: str) -> SearchOutput:
        """Up to `SEARCH_LIMIT` bookings of the next 7 days by guest name or reference, cancelled
        included; names compare as the Guests page groups them (`guest_key`: case, accents and
        spacing), so that "zoe" finds Zoé Huber in both search boxes."""
        wanted = guest_key(q)
        if not wanted:
            return SearchOutput(reservations=())
        today = self.clock().astimezone(self.tz).date()
        start, _ = day_bounds(today, self.tz)
        _, end = day_bounds(today + timedelta(days=dashboard.NEXT_DAYS - 1), self.tz)
        async with merchant_conn(self.engine, slug) as conn:
            await load_merchant(conn, slug, merchant_side=True)
            ahead = await self._managed(conn, reservation_start >= start, reservation_start < end)
        found = [b for b in ahead if wanted in guest_key(b.guest_name) or wanted in guest_key(b.id)]
        return SearchOutput(reservations=tuple(found[:SEARCH_LIMIT]))

    async def reservation(self, slug: str, reservation_id: str) -> ManagedReservation:
        async with merchant_conn(self.engine, slug) as conn:
            await load_merchant(conn, slug, merchant_side=True)
            return await self._current(conn, reservation_id)

    # Helpers

    async def _locked(self, conn: AsyncConnection, reservation_id: str) -> AnyRow:
        """The booking to move, locked; `NotFound` when there is none."""
        current = (
            await conn.execute(
                select(reservations_t)
                .where(reservations_t.c.id == reservation_id.strip().upper())
                .with_for_update(of=reservations_t)
            )
        ).one_or_none()
        if current is None:
            raise NotFound(f"No booking {reservation_id!r}")
        return current

    def _check_movable(self, current: AnyRow, starts_at: datetime) -> None:
        if current.status != "confirmed":
            raise SlotUnavailable(f"Booking {current.id} is cancelled")
        check_not_started(current.id, current.during.lower, now=self.clock())
        if starts_at.astimezone(self.tz).date() != current.during.lower.astimezone(self.tz).date():
            raise InvalidInput("A booking moves within its day")

    async def _tell_move(
        self, conn: AsyncConnection, slug: str, current: AnyRow, starts_at: datetime
    ) -> None:
        """The guest's notice, for a booking a guest made, and the move's activity line."""
        if current.via != "merchant":  # a guest made it: tell them
            await notices.notify_move(
                conn, slug, current.id, current.guest_name, starts_at, self.tz
            )
        await activity.record_booking(
            conn,
            slug,
            "moved",
            "merchant",
            current.id,
            current.guest_name,
            current.party_size,
            starts_at,
            at=self.clock(),
        )
        log.info(
            "reservation moved by merchant", extra={"merchant": slug, "reservation": current.id}
        )

    async def _current(self, conn: AsyncConnection, reservation_id: str) -> ManagedReservation:
        """The booking as it is now in this transaction; `NotFound` when there is none."""
        found = await self._managed(conn, reservations_t.c.id == reservation_id)
        if not found:
            raise NotFound(f"No booking {reservation_id!r}")
        return found[0]

    async def _managed(
        self, conn: AsyncConnection, *conditions: ColumnElement[bool]
    ) -> list[ManagedReservation]:
        query = (
            select(*_MANAGED_COLUMNS)
            .join(tables_t, tables_t.c.id == reservations_t.c.table_id)
            .where(*conditions)
            .order_by(reservation_start, reservations_t.c.id)
        )
        return [
            ManagedReservation(
                id=row.id,
                date=row.starts_at.astimezone(self.tz).date(),
                time=hhmm(row.starts_at, self.tz),
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
