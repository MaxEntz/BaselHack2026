"""`PgAgentService`, the Postgres implementation of the `AgentService` protocol.

The protocol lives in `agent.protocol` and is re-exported here, with `MerchantCancellation` and
`AgentStatus` (`core.models`): callers import them from `agent.service`. Claude's side books
here; a booking's write on a table, and the rules a write checks first, are `agent.writes`'. The
merchant's side delegates to `agent.admin` and `agent.site_forms`, each call in one
`merchant_conn` transaction.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Literal, NoReturn, Self, overload
from zoneinfo import ZoneInfo

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from agent import activity, admin, notices, site_forms
from agent.activity import GUEST_SOURCE
from agent.guests import same_guest
from agent.merchant import Merchant, load_merchant
from agent.protocol import AgentService as AgentService
from agent.protocol import MerchantCancellation as MerchantCancellation
from agent.times import day_and_time, day_bounds, during, parse_hhmm, utc
from agent.writes import (
    candidate_tables,
    check_new_start,
    check_not_started,
    claim_update_key,
    first_free,
    moved_to,
)
from core.availability import find_slots
from core.config import get_settings
from core.db import (
    AnyRow,
    blocked_between,
    busy_between,
    merchant_conn,
    merchants_t,
    next_reservation_id,
    reservation_confirmed,
    reservation_start,
    reservations_t,
    tables_t,
)
from core.errors import InvalidInput, NotFound, SlotUnavailable
from core.models import (
    ActivityKind,
    AdminReservationRow,
    AgentRequest,
    BlockedTime,
    GuestRow,
    KnowledgeChange,
    KnowledgeNote,
    MerchantSettings,
    Policy,
    PriceLevel,
    Reservation,
    ReservationStatus,
    Rules,
    Service,
    Slot,
    Stats,
    TableSpec,
    Tag,
    Via,
)
from core.models import AgentStatus as AgentStatus

log = logging.getLogger(__name__)

Clock = Callable[[], datetime]

_RESERVATION_COLUMNS = (
    reservations_t.c.id,
    reservations_t.c.table_id,
    reservations_t.c.during,
    reservations_t.c.party_size,
    reservations_t.c.status,
    reservations_t.c.guest_name,
    reservations_t.c.via,
    reservations_t.c.idempotency_key,
    tables_t.c.label,
)


@dataclass(frozen=True)
class _Booking:
    """A booking as the service answers it: read from a row, or as a write leaves it."""

    id: str
    table_label: str
    starts_at: datetime
    party_size: int
    status: ReservationStatus
    guest_name: str
    via: Via

    @classmethod
    def of(cls, row: AnyRow) -> Self:
        """From a reservation joined with its table label (`_RESERVATION_COLUMNS`)."""
        return cls(
            id=row.id,
            table_label=row.label,
            starts_at=row.during.lower,
            party_size=row.party_size,
            status=row.status,
            guest_name=row.guest_name,
            via=row.via,
        )


class PgAgentService:
    """`AgentService` on Postgres, one schema per merchant (`core.db.merchant_conn`).

    `clock` returns the current time (tests inject a fixed one). An unknown slug, or a
    merchant still `building` (onboarding not finished), is `NotFound`. A `down` merchant
    still takes bookings here: the gateway stops Claude for it (#13), while its own website
    keeps working during an outage.
    """

    def __init__(
        self, engine: AsyncEngine, *, tz: ZoneInfo | None = None, clock: Clock | None = None
    ) -> None:
        self.engine = engine
        self.tz = tz or ZoneInfo(get_settings().merchant_tz)
        self.clock = clock or (lambda: datetime.now(self.tz))

    # Claude side (#8)

    async def get_availability(
        self, slug: str, day: date, party_size: int, time: str | None = None
    ) -> list[Slot]:
        requested = parse_hhmm(time) if time is not None else None
        async with merchant_conn(self.engine, slug) as conn:
            merchant = await load_merchant(conn, slug)
            merchant.check_party(party_size)
            day_start, day_end = day_bounds(day, self.tz)
            busy = await busy_between(conn, day_start, utc(day_end) + merchant.slot_length)
            blocked = await blocked_between(conn, day_start, day_end)
            slots = find_slots(
                services=merchant.services,
                tables=merchant.tables,
                busy=busy,
                blocked=set(blocked),
                day=day,
                tz=self.tz,
                party_size=party_size,
                requested=requested,
                now=self.clock(),
                slot_length=merchant.slot_length,
            )
        return [Slot(starts_at=start, table_label=table.label) for start, table in slots]

    async def create_reservation(
        self,
        slug: str,
        starts_at: datetime,
        party_size: int,
        guest_name: str,
        idempotency_key: str,
        via: Via,
    ) -> Reservation:
        async with merchant_conn(self.engine, slug) as conn:
            merchant = await load_merchant(conn, slug)
            # A replay returns the first result, even if the slot has passed or filled since.
            if existing := await self._by_key(conn, idempotency_key):
                return self._replayed(slug, existing, starts_at, party_size, guest_name)
            merchant.check_party(party_size)
            await self._check_new_start(conn, merchant, starts_at)

            reservation_id = await next_reservation_id(conn)
            span = during(starts_at, merchant.slot_length)
            table = await first_free(
                conn,
                candidate_tables(merchant.tables, party_size),
                party_size,
                lambda t: insert(reservations_t).values(
                    id=reservation_id,
                    table_id=t.id,
                    guest_name=guest_name,
                    party_size=party_size,
                    during=span,
                    via=via,
                    idempotency_key=idempotency_key,
                ),
            )
            if table is not None:
                booked = _Booking(
                    id=reservation_id,
                    table_label=table.label,
                    starts_at=starts_at,
                    party_size=party_size,
                    status="confirmed",
                    guest_name=guest_name,
                    via=via,
                )
                await self._record(conn, slug, "new_booking", via, booked)
                log.info(
                    "reservation created",
                    extra={"merchant": slug, "reservation": reservation_id, "via": via},
                )
                return self._reservation(slug, booked)

            # A concurrent retry with the same key won; this also covers the case where its
            # row hit us as 23P01 first.
            if existing := await self._by_key(conn, idempotency_key):
                return self._replayed(slug, existing, starts_at, party_size, guest_name)
        raise SlotUnavailable(
            f"No table left at {merchant.name} at {day_and_time(starts_at, self.tz)}"
        )

    async def update_reservation(
        self,
        slug: str,
        reservation_id: str,
        guest_name: str,
        idempotency_key: str,
        starts_at: datetime | None = None,
        party_size: int | None = None,
    ) -> Reservation:
        async with merchant_conn(self.engine, slug) as conn:
            merchant = await load_merchant(conn, slug)
            current = await self._own_booking(conn, reservation_id, guest_name)
            # A key already applied returns the booking as it is now: a late retry of an
            # older change must not undo a newer one (#78).
            if not await claim_update_key(
                conn, slug, idempotency_key, current.id, starts_at, party_size
            ):
                return self._from_row(slug, current)
            if current.status != "confirmed":
                _not_found(reservation_id)
            check_not_started(current.id, current.during.lower, now=self.clock())
            new_start = starts_at or current.during.lower
            new_party = party_size or current.party_size
            merchant.check_party(new_party)
            if utc(new_start) != utc(current.during.lower):
                await self._check_new_start(conn, merchant, new_start)

            span = during(new_start, merchant.slot_length)
            table = await first_free(
                conn,
                candidate_tables(merchant.tables, new_party, current_id=current.table_id),
                new_party,
                moved_to(current.id, span, party_size=new_party),
            )
            if table is None:
                # Inside the transaction: it rolls back, and the key claimed above with it.
                raise SlotUnavailable(
                    f"No table left at {merchant.name} at {day_and_time(new_start, self.tz)}"
                )
            before = _Booking.of(current)
            after = replace(
                before, table_label=table.label, starts_at=new_start, party_size=new_party
            )
            await self._record_change(conn, slug, before, after)
            log.info(
                "reservation updated",
                extra={"merchant": slug, "reservation": current.id, "key": idempotency_key},
            )
            return self._reservation(slug, after)

    @overload
    async def cancel_reservation(
        self, slug: str, reservation_id: str, *, by: Literal["guest"], guest_name: str
    ) -> Reservation: ...

    @overload
    async def cancel_reservation(
        self, slug: str, reservation_id: str, *, by: Literal["merchant"], guest_name: None = None
    ) -> Reservation: ...

    async def cancel_reservation(
        self,
        slug: str,
        reservation_id: str,
        *,
        by: Literal["guest", "merchant"],
        guest_name: str | None = None,
    ) -> Reservation:
        if by == "merchant":
            return (await self.cancel_by_merchant(slug, reservation_id)).reservation
        if guest_name is None:
            raise InvalidInput("guest_name is required to cancel a booking as the guest")
        async with merchant_conn(self.engine, slug) as conn:
            await load_merchant(conn, slug)
            current = await self._own_booking(conn, reservation_id, guest_name)
            cancelled = replace(_Booking.of(current), status="cancelled")
            if current.status == "confirmed":
                check_not_started(current.id, current.during.lower, now=self.clock())
                await conn.execute(
                    update(reservations_t)
                    .where(reservations_t.c.id == current.id)
                    .values(status="cancelled", cancelled_by="guest")
                )
                await self._record(conn, slug, "cancelled", GUEST_SOURCE, cancelled)
                log.info(
                    "reservation cancelled", extra={"merchant": slug, "reservation": current.id}
                )
        return self._reservation(slug, cancelled)

    async def find_guest_reservations(self, guest_name: str) -> tuple[list[Reservation], list[str]]:
        """Upcoming confirmed bookings at every live merchant, and the guest's notices
        (`notices.returned_to`).

        Names are compared in Python with `same_guest` on every upcoming booking: fine for the
        POC's handful of merchants (past that, store a normalized key at insert time).
        """
        now = self.clock()
        async with self.engine.connect() as conn:
            slugs = list(
                await conn.scalars(
                    select(merchants_t.c.slug)
                    .where(merchants_t.c.status == "live")
                    .order_by(merchants_t.c.slug)
                )
            )
        reservations: list[Reservation] = []
        for slug in slugs:
            async with merchant_conn(self.engine, slug) as conn:
                rows = await conn.execute(
                    select(*_RESERVATION_COLUMNS)
                    .join(tables_t, tables_t.c.id == reservations_t.c.table_id)
                    .where(reservation_confirmed, reservation_start > now)
                )
                reservations += [
                    self._from_row(slug, row)
                    for row in rows
                    if same_guest(guest_name, row.guest_name)
                ]

        async with self.engine.begin() as conn:
            messages = await notices.returned_to(conn, guest_name, now)
        reservations.sort(key=lambda r: (r.starts_at, r.merchant, r.id))
        return reservations, messages

    # Merchant side (#9): the queries live in `agent.admin`.

    async def list_reservations(
        self, slug: str, day: date, *, include_cancelled: bool = False
    ) -> list[AdminReservationRow]:
        async with merchant_conn(self.engine, slug) as conn:
            await load_merchant(conn, slug, merchant_side=True)
            return await admin.list_reservations(
                conn, day, self.tz, include_cancelled=include_cancelled
            )

    async def get_stats(self, slug: str, day: date) -> Stats:
        async with merchant_conn(self.engine, slug) as conn:
            await load_merchant(conn, slug, merchant_side=True)
            return await admin.get_stats(conn, day, self.tz)

    async def list_guests(self, slug: str, query: str = "") -> list[GuestRow]:
        async with merchant_conn(self.engine, slug) as conn:
            await load_merchant(conn, slug, merchant_side=True)
            return await admin.list_guests(conn, query, self.clock(), self.tz)

    async def block_slot(self, slug: str, starts_at: datetime) -> None:
        async with merchant_conn(self.engine, slug) as conn:
            merchant = await load_merchant(conn, slug, merchant_side=True)
            await admin.block_slot(
                conn, slug, merchant.services, starts_at, self.tz, at=self.clock()
            )
        log.info("slot blocked", extra={"merchant": slug, "starts_at": starts_at.isoformat()})

    async def unblock_slot(self, slug: str, starts_at: datetime) -> None:
        async with merchant_conn(self.engine, slug) as conn:
            await load_merchant(conn, slug, merchant_side=True)
            await admin.unblock_slot(conn, slug, starts_at, at=self.clock())
        log.info("slot unblocked", extra={"merchant": slug, "starts_at": starts_at.isoformat()})

    async def update_policy(
        self,
        slug: str,
        max_party_size: int | None = None,
        services: Sequence[Service] | None = None,
    ) -> Policy:
        async with merchant_conn(self.engine, slug) as conn:
            await load_merchant(conn, slug, merchant_side=True)
            policy = await admin.update_policy(
                conn, slug, max_party_size, services, at=self.clock()
            )
        log.info("policy updated", extra={"merchant": slug})
        return policy

    async def update_knowledge(
        self, slug: str, remove: Sequence[str], add: Sequence[KnowledgeNote]
    ) -> KnowledgeChange:
        async with merchant_conn(self.engine, slug) as conn:
            await load_merchant(conn, slug, merchant_side=True)
            change = await admin.update_knowledge(conn, slug, remove, add, at=self.clock())
        log.info("knowledge updated", extra={"merchant": slug})
        return change

    async def update_tables(self, slug: str, tables: Sequence[TableSpec]) -> list[TableSpec]:
        async with merchant_conn(self.engine, slug) as conn:
            await load_merchant(conn, slug, merchant_side=True)
            plan = await admin.update_tables(conn, slug, tables, self.clock(), self.tz)
        log.info("floor plan updated", extra={"merchant": slug, "tables": len(plan)})
        return plan

    async def set_status(self, slug: str, status: AgentStatus) -> None:
        async with merchant_conn(self.engine, slug) as conn:
            await load_merchant(conn, slug, merchant_side=True)
            await admin.pause_or_resume(conn, slug, status, at=self.clock())
        log.info("agent paused" if status == "down" else "agent resumed", extra={"merchant": slug})

    async def list_agent_requests(
        self, slug: str, day: date, *, refused: bool = False
    ) -> list[AgentRequest]:
        async with merchant_conn(self.engine, slug) as conn:
            merchant = await load_merchant(conn, slug, merchant_side=True)
            return await admin.list_agent_requests(
                conn, slug, day, self.tz, refused=refused, max_party_size=merchant.max_party_size
            )

    # The management site's forms (#133): the queries live in `agent.site_forms`.

    async def get_policy(self, slug: str) -> Policy:
        async with merchant_conn(self.engine, slug) as conn:
            await load_merchant(conn, slug, merchant_side=True)
            return await site_forms.get_policy(conn)

    async def get_tables(self, slug: str) -> list[TableSpec]:
        async with merchant_conn(self.engine, slug) as conn:
            await load_merchant(conn, slug, merchant_side=True)
            return await site_forms.get_tables(conn, slug)

    async def get_rules(self, slug: str) -> Rules:
        async with merchant_conn(self.engine, slug) as conn:
            await load_merchant(conn, slug, merchant_side=True)
            return await site_forms.get_rules(conn, slug)

    async def update_rules(
        self,
        slug: str,
        max_party_size: int,
        fallback_url: str,
        cancellation_policy: str | None,
    ) -> Rules:
        async with merchant_conn(self.engine, slug) as conn:
            await load_merchant(conn, slug, merchant_side=True)
            rules = await site_forms.update_rules(
                conn, slug, max_party_size, fallback_url, cancellation_policy, at=self.clock()
            )
        log.info("booking rules updated", extra={"merchant": slug})
        return rules

    async def get_settings(self, slug: str) -> MerchantSettings:
        async with merchant_conn(self.engine, slug) as conn:
            await load_merchant(conn, slug, merchant_side=True)
            return await site_forms.get_settings(conn, slug)

    async def update_settings(
        self,
        slug: str,
        name: str,
        city: str | None,
        price_level: PriceLevel,
        tags: Sequence[Tag],
    ) -> MerchantSettings:
        async with merchant_conn(self.engine, slug) as conn:
            await load_merchant(conn, slug, merchant_side=True)
            settings = await site_forms.update_settings(
                conn, slug, name, city, price_level, tags, at=self.clock()
            )
        log.info("settings updated", extra={"merchant": slug})
        return settings

    async def list_blocked(self, slug: str) -> list[BlockedTime]:
        async with merchant_conn(self.engine, slug) as conn:
            await load_merchant(conn, slug, merchant_side=True)
            return await site_forms.list_blocked(conn, self.clock(), self.tz)

    async def set_blocked(self, slug: str, day: date, times: Sequence[str]) -> list[BlockedTime]:
        async with merchant_conn(self.engine, slug) as conn:
            merchant = await load_merchant(conn, slug, merchant_side=True)
            now = self.clock()
            await site_forms.set_blocked(
                conn, slug, merchant.services, day, times, self.tz, now=now, at=now
            )
            blocked = await site_forms.list_blocked(conn, now, self.tz)
        log.info("blocked times set", extra={"merchant": slug, "date": day.isoformat()})
        return blocked

    async def cancel_by_merchant(self, slug: str, reservation_id: str) -> MerchantCancellation:
        async with merchant_conn(self.engine, slug) as conn:
            await load_merchant(conn, slug, merchant_side=True)
            row, outcome = await admin.cancel_by_merchant(
                conn, slug, reservation_id, self.tz, self.clock()
            )
        log.info(
            "reservation cancelled by merchant",
            extra={"merchant": slug, "reservation": row.id, "cancel_outcome": outcome},
        )
        return MerchantCancellation(reservation=self._from_row(slug, row), outcome=outcome)

    # Helpers

    async def _check_new_start(
        self, conn: AsyncConnection, merchant: Merchant, start: datetime
    ) -> None:
        await check_new_start(
            conn,
            merchant.services,
            start,
            now=self.clock(),
            tz=self.tz,
            merchant_name=merchant.name,
        )

    async def _record(
        self, conn: AsyncConnection, slug: str, kind: ActivityKind, source: Via, booking: _Booking
    ) -> None:
        """The booking's activity line, as the change left it (#135)."""
        await activity.record_booking(
            conn,
            slug,
            kind,
            source,
            booking.id,
            booking.guest_name,
            booking.party_size,
            booking.starts_at,
            at=self.clock(),
        )

    async def _record_change(
        self, conn: AsyncConnection, slug: str, before: _Booking, after: _Booking
    ) -> None:
        """A guest's change comes through an agent (the website only books), and an update that
        changes nothing writes no line (#135)."""
        if (utc(after.starts_at), after.party_size) != (utc(before.starts_at), before.party_size):
            await self._record(conn, slug, "moved", GUEST_SOURCE, after)

    async def _by_key(self, conn: AsyncConnection, key: str) -> AnyRow | None:
        return (
            await conn.execute(
                select(*_RESERVATION_COLUMNS)
                .join(tables_t, tables_t.c.id == reservations_t.c.table_id)
                .where(reservations_t.c.idempotency_key == key)
            )
        ).one_or_none()

    async def _own_booking(
        self, conn: AsyncConnection, reservation_id: str, guest_name: str
    ) -> AnyRow:
        """The booking, locked, if it exists under that name; `NotFound` otherwise.

        A wrong name looks exactly like a missing booking: it does not reveal that it exists.
        """
        row = (
            await conn.execute(
                select(*_RESERVATION_COLUMNS)
                .join(tables_t, tables_t.c.id == reservations_t.c.table_id)
                .where(reservations_t.c.id == reservation_id.strip().upper())
                .with_for_update(of=reservations_t)
            )
        ).one_or_none()
        if row is None or not same_guest(guest_name, row.guest_name):
            _not_found(reservation_id)
        return row

    def _replayed(
        self, slug: str, row: AnyRow, starts_at: datetime, party_size: int, guest_name: str
    ) -> Reservation:
        same = (utc(row.during.lower), row.party_size) == (utc(starts_at), party_size)
        if not same or not same_guest(guest_name, row.guest_name):
            raise InvalidInput("idempotency_key reused with different parameters")
        return self._from_row(slug, row)

    def _from_row(self, slug: str, row: AnyRow) -> Reservation:
        return self._reservation(slug, _Booking.of(row))

    def _reservation(self, slug: str, booking: _Booking) -> Reservation:
        return Reservation(
            id=booking.id,
            merchant=slug,
            starts_at=booking.starts_at.astimezone(self.tz),
            party_size=booking.party_size,
            table_label=booking.table_label,
            status=booking.status,
            guest_name=booking.guest_name,
            via=booking.via,
        )


def _not_found(reservation_id: str) -> NoReturn:
    raise NotFound(f"No booking {reservation_id!r} under that name")
