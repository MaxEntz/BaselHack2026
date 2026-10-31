"""Async business-service contract shared by the gateway and platform, and its Postgres
implementation (`PgAgentService`).

Double booking is prevented by the exclusion constraint of `reservations`: each candidate
table is tried in its own SAVEPOINT, and an exclusion violation (23P01) moves on to the
next one. Before writing, the attempt locks the table's row in `tables`. That lock does not
prevent double booking (the constraint does), it only queues writers on the same table:
without it, concurrent writers wait on each other's uncommitted rows while checking the
constraint and deadlock (40P01). Rolling back the SAVEPOINT releases the lock, so a
transaction never holds one table while waiting for another.
"""

import logging
from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timedelta
from typing import Any, Literal, NoReturn, Protocol, overload
from zoneinfo import ZoneInfo

from sqlalchemy import Row, func, insert, select, update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.sql import Executable
from sqlalchemy.sql.elements import ColumnElement

from core.availability import (
    BusyInterval,
    TableRow,
    bookable_starts,
    find_slots,
    is_bookable_start,
)
from core.config import get_settings
from core.db import (
    blocked_slots_t,
    guest_notices_t,
    merchant_conn,
    merchants_t,
    next_reservation_id,
    policy_t,
    reservations_t,
    tables_t,
)
from core.errors import InvalidInput, NotFound, PartyTooLarge, SlotUnavailable
from core.models import (
    AdminReservationRow,
    Policy,
    Reservation,
    Service,
    Slot,
    Stats,
    TableSpec,
    Via,
)

log = logging.getLogger(__name__)

Clock = Callable[[], datetime]
# Any row shape: reservations joined with their table label, or the policy row.
_Row = Row[*tuple[Any, ...]]

EXCLUSION_VIOLATION = "23P01"
IDEMPOTENCY_CONSTRAINT = "reservations_idempotency_key_key"

Outcome = Literal["done", "taken", "key_taken"]


class AgentService(Protocol):
    """Implementations raise core.errors.AgentError for business failures."""

    async def get_availability(
        self, slug: str, day: date, party_size: int, time: str | None = None
    ) -> list[Slot]: ...

    async def create_reservation(
        self,
        slug: str,
        starts_at: datetime,
        party_size: int,
        guest_name: str,
        idempotency_key: str,
        via: Via,
    ) -> Reservation: ...

    async def update_reservation(
        self,
        slug: str,
        reservation_id: str,
        guest_name: str,
        idempotency_key: str,
        starts_at: datetime | None = None,
        party_size: int | None = None,
    ) -> Reservation: ...

    @overload
    async def cancel_reservation(
        self, slug: str, reservation_id: str, *, by: Literal["guest"], guest_name: str
    ) -> Reservation: ...

    @overload
    async def cancel_reservation(
        self, slug: str, reservation_id: str, *, by: Literal["merchant"], guest_name: None = None
    ) -> Reservation: ...

    async def find_guest_reservations(
        self, guest_name: str
    ) -> tuple[list[Reservation], list[str]]: ...

    async def list_reservations(self, slug: str, day: date) -> list[AdminReservationRow]: ...

    async def get_stats(self, slug: str, day: date) -> Stats: ...

    async def block_slot(self, slug: str, starts_at: datetime) -> None: ...

    async def unblock_slot(self, slug: str, starts_at: datetime) -> None: ...

    async def update_policy(
        self,
        slug: str,
        max_party_size: int | None = None,
        services: Sequence[Service] | None = None,
    ) -> Policy: ...

    async def update_tables(self, slug: str, tables: Sequence[TableSpec]) -> list[TableSpec]: ...


def same_guest(given: str, booked: str) -> bool:
    """Guest names match ignoring case and spacing: Claude may not repeat them verbatim."""
    return _normalize(given) == _normalize(booked)


def _normalize(name: str) -> str:
    return " ".join(name.split()).casefold()


def _normalized_column(column: ColumnElement[str]) -> ColumnElement[str]:
    """SQL side of `_normalize`, for lookups by guest name."""
    return func.lower(func.regexp_replace(func.btrim(column), r"\s+", " ", "g"))


def candidate_tables(
    tables: Sequence[TableRow], party_size: int, current_id: int | None = None
) -> list[TableRow]:
    """Tables that seat the party, smallest first then by label; the current one first."""
    fitting = sorted((t for t in tables if t.seats >= party_size), key=lambda t: (t.seats, t.label))
    current = [t for t in fitting if t.id == current_id]
    return current + [t for t in fitting if t.id != current_id]


def check_start(
    services: Sequence[Service], start: datetime, *, now: datetime, tz: ZoneInfo
) -> None:
    """Raise `SlotUnavailable` unless `start` is a future start time of a service."""
    if not is_bookable_start(services, start, tz):
        local = start.astimezone(tz)
        valid = ", ".join(s.strftime("%H:%M") for s in bookable_starts(services, local.date(), tz))
        raise SlotUnavailable(
            f"{local:%H:%M} is not a start time on {local:%Y-%m-%d}; valid times: {valid or 'none'}"
        )
    if start <= now:
        raise SlotUnavailable(f"{start.astimezone(tz):%Y-%m-%d %H:%M} is in the past")


def _sqlstate(error: DBAPIError) -> str | None:
    state: str | None = getattr(error.orig, "sqlstate", None)
    return state


def _constraint(error: DBAPIError) -> str | None:
    diag = getattr(error.orig, "diag", None)
    name: str | None = getattr(diag, "constraint_name", None)
    return name


async def _attempt(conn: AsyncConnection, table_id: int, statement: Executable) -> Outcome:
    """Write a booking on `table_id` in a SAVEPOINT, so a conflict leaves the transaction usable.

    `taken`: the table is booked over that range (exclusion violation). `key_taken`: a
    concurrent request with the same idempotency key won. Anything else is raised.
    """
    try:
        async with conn.begin_nested():
            # Queue behind other writers on this table (see the module docstring).
            await conn.execute(
                select(tables_t.c.id).where(tables_t.c.id == table_id).with_for_update()
            )
            await conn.execute(statement)
    except DBAPIError as error:
        if _sqlstate(error) == EXCLUSION_VIOLATION:
            return "taken"
        if _constraint(error) == IDEMPOTENCY_CONSTRAINT:
            return "key_taken"
        raise
    return "done"


class _Merchant:
    """What one operation needs from the registry and the merchant's policy."""

    def __init__(self, name: str, policy: _Row, tables: Sequence[TableRow]) -> None:
        self.name = name
        self.max_party_size: int = policy.max_party_size
        self.slot_length = timedelta(minutes=policy.slot_length_min)
        self.services = [Service.model_validate(s) for s in policy.services]
        self.tables = tables

    def check_party(self, party_size: int) -> None:
        """Too large for the policy or for every table: the guest gets the fallback link."""
        largest = max((t.seats for t in self.tables), default=0)
        if party_size > self.max_party_size or party_size > largest:
            limit = min(self.max_party_size, largest)
            raise PartyTooLarge(f"{self.name} takes online bookings for up to {limit} guests")


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


class PgAgentService:
    """`AgentService` on Postgres, one schema per merchant (`core.db.merchant_conn`).

    `clock` returns the current time (tests inject a fixed one). The registry status is the
    gateway's business (#13): here an unknown slug is `NotFound`, whatever its status.
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
        requested = _parse_hhmm(time) if time is not None else None
        async with merchant_conn(self.engine, slug) as conn:
            merchant = await self._load(conn, slug)
            merchant.check_party(party_size)
            day_start = datetime.combine(day, datetime.min.time(), tzinfo=self.tz)
            day_end = day_start + timedelta(days=1)
            busy_rows = await conn.execute(
                select(reservations_t.c.table_id, reservations_t.c.during).where(
                    reservations_t.c.status == "confirmed",
                    reservations_t.c.during.overlaps(
                        Range(day_start, day_end + merchant.slot_length)
                    ),
                )
            )
            blocked = await conn.scalars(
                select(blocked_slots_t.c.starts_at).where(
                    blocked_slots_t.c.starts_at >= day_start, blocked_slots_t.c.starts_at < day_end
                )
            )
            slots = find_slots(
                services=merchant.services,
                tables=merchant.tables,
                busy=[BusyInterval(r.table_id, r.during.lower, r.during.upper) for r in busy_rows],
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
            merchant = await self._load(conn, slug)
            # A replay returns the first result, even if the slot has passed or filled since.
            if existing := await self._by_key(conn, idempotency_key):
                return self._replayed(slug, existing, starts_at, party_size, guest_name)
            merchant.check_party(party_size)
            await self._check_new_start(conn, merchant, starts_at)

            reservation_id = await next_reservation_id(conn)
            during = Range(starts_at, starts_at + merchant.slot_length, bounds="[)")
            for table in candidate_tables(merchant.tables, party_size):
                outcome = await _attempt(
                    conn,
                    table.id,
                    insert(reservations_t).values(
                        id=reservation_id,
                        table_id=table.id,
                        guest_name=guest_name,
                        party_size=party_size,
                        during=during,
                        via=via,
                        idempotency_key=idempotency_key,
                    ),
                )
                if outcome == "taken":
                    continue  # booked meanwhile: next table
                if outcome == "key_taken":
                    break  # a concurrent retry with the same key won: return its booking
                log.info(
                    "reservation created",
                    extra={"merchant": slug, "reservation": reservation_id, "via": via},
                )
                log.debug("reservation %s for %r", reservation_id, guest_name)
                return self._reservation(
                    slug,
                    reservation_id,
                    table.label,
                    during,
                    party_size,
                    "confirmed",
                    guest_name,
                    via,
                )

            # Also covers the case where the concurrent retry's row hit us as 23P01 first.
            if existing := await self._by_key(conn, idempotency_key):
                return self._replayed(slug, existing, starts_at, party_size, guest_name)
        raise SlotUnavailable(f"No table left at {merchant.name} at {_hhmm(starts_at, self.tz)}")

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
            merchant = await self._load(conn, slug)
            current = await self._own_booking(conn, reservation_id, guest_name)
            if current.status != "confirmed":
                _not_found(reservation_id)
            new_start = starts_at or current.during.lower
            new_party = party_size or current.party_size
            merchant.check_party(new_party)
            if new_start != current.during.lower:
                await self._check_new_start(conn, merchant, new_start)
            elif new_start <= self.clock():
                raise SlotUnavailable(f"Booking {current.id} has already started")

            during = Range(new_start, new_start + merchant.slot_length, bounds="[)")
            for table in candidate_tables(merchant.tables, new_party, current_id=current.table_id):
                outcome = await _attempt(
                    conn,
                    table.id,
                    update(reservations_t)
                    .where(reservations_t.c.id == current.id)
                    .values(table_id=table.id, during=during, party_size=new_party),
                )
                if outcome != "done":
                    continue
                log.info(
                    "reservation updated",
                    extra={"merchant": slug, "reservation": current.id, "key": idempotency_key},
                )
                return self._reservation(
                    slug,
                    current.id,
                    table.label,
                    during,
                    new_party,
                    "confirmed",
                    current.guest_name,
                    current.via,
                )
        raise SlotUnavailable(f"No table left at {merchant.name} at {_hhmm(new_start, self.tz)}")

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
        if by == "merchant" or guest_name is None:
            raise NotImplementedError("Merchant cancellation and its guest notice come with #9")
        async with merchant_conn(self.engine, slug) as conn:
            await self._load(conn, slug)
            current = await self._own_booking(conn, reservation_id, guest_name)
            if current.status == "confirmed":
                await conn.execute(
                    update(reservations_t)
                    .where(reservations_t.c.id == current.id)
                    .values(status="cancelled", cancelled_by="guest")
                )
                log.info(
                    "reservation cancelled", extra={"merchant": slug, "reservation": current.id}
                )
        return self._reservation(
            slug,
            current.id,
            current.label,
            current.during,
            current.party_size,
            "cancelled",
            current.guest_name,
            current.via,
        )

    async def find_guest_reservations(self, guest_name: str) -> tuple[list[Reservation], list[str]]:
        """Upcoming confirmed bookings at every live merchant, and the guest's new notices.

        Notices are marked delivered in the same statement that returns them: each one is
        returned once. A simple loop over merchants is fine for the POC's handful of them.
        """
        name = _normalize(guest_name)
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
                    .where(
                        reservations_t.c.status == "confirmed",
                        func.lower(reservations_t.c.during) > now,
                        _normalized_column(reservations_t.c.guest_name) == name,
                    )
                )
                reservations += [self._from_row(slug, row) for row in rows]

        async with self.engine.begin() as conn:
            delivered = await conn.execute(
                update(guest_notices_t)
                .where(
                    guest_notices_t.c.delivered_at.is_(None),
                    _normalized_column(guest_notices_t.c.guest_name) == name,
                )
                .values(delivered_at=now)
                .returning(guest_notices_t.c.id, guest_notices_t.c.message)
            )
            notices = [row.message for row in sorted(delivered, key=lambda row: row.id)]
        reservations.sort(key=lambda r: (r.starts_at, r.merchant, r.id))
        return reservations, notices

    # Merchant side: #9 implements these on the same class.

    async def list_reservations(self, slug: str, day: date) -> list[AdminReservationRow]:
        raise NotImplementedError("#9")

    async def get_stats(self, slug: str, day: date) -> Stats:
        raise NotImplementedError("#9")

    async def block_slot(self, slug: str, starts_at: datetime) -> None:
        raise NotImplementedError("#9")

    async def unblock_slot(self, slug: str, starts_at: datetime) -> None:
        raise NotImplementedError("#9")

    async def update_policy(
        self,
        slug: str,
        max_party_size: int | None = None,
        services: Sequence[Service] | None = None,
    ) -> Policy:
        raise NotImplementedError("#9")

    async def update_tables(self, slug: str, tables: Sequence[TableSpec]) -> list[TableSpec]:
        raise NotImplementedError("#9")

    # Helpers

    async def _load(self, conn: AsyncConnection, slug: str) -> _Merchant:
        name = await conn.scalar(select(merchants_t.c.name).where(merchants_t.c.slug == slug))
        if name is None:
            raise NotFound(f"Unknown merchant: {slug!r}")
        policy = (await conn.execute(select(policy_t))).one()
        tables = [
            TableRow(id=row.id, label=row.label, seats=row.seats)
            for row in await conn.execute(select(tables_t.c.id, tables_t.c.label, tables_t.c.seats))
        ]
        return _Merchant(name, policy, tables)

    async def _check_new_start(
        self, conn: AsyncConnection, merchant: _Merchant, start: datetime
    ) -> None:
        check_start(merchant.services, start, now=self.clock(), tz=self.tz)
        blocked = await conn.scalar(
            select(blocked_slots_t.c.starts_at).where(blocked_slots_t.c.starts_at == start)
        )
        if blocked is not None:
            raise SlotUnavailable(f"{merchant.name} has blocked {_hhmm(start, self.tz)}")

    async def _by_key(self, conn: AsyncConnection, key: str) -> _Row | None:
        return (
            await conn.execute(
                select(*_RESERVATION_COLUMNS)
                .join(tables_t, tables_t.c.id == reservations_t.c.table_id)
                .where(reservations_t.c.idempotency_key == key)
            )
        ).one_or_none()

    async def _own_booking(
        self, conn: AsyncConnection, reservation_id: str, guest_name: str
    ) -> _Row:
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
        self, slug: str, row: _Row, starts_at: datetime, party_size: int, guest_name: str
    ) -> Reservation:
        if (row.during.lower, row.party_size) != (starts_at, party_size) or not same_guest(
            guest_name, row.guest_name
        ):
            raise InvalidInput("idempotency_key reused with different parameters")
        return self._from_row(slug, row)

    def _from_row(self, slug: str, row: _Row) -> Reservation:
        return self._reservation(
            slug,
            row.id,
            row.label,
            row.during,
            row.party_size,
            row.status,
            row.guest_name,
            row.via,
        )

    def _reservation(
        self,
        slug: str,
        reservation_id: str,
        table_label: str,
        during: Range[datetime],
        party_size: int,
        status: Literal["confirmed", "cancelled"],
        guest_name: str,
        via: Via,
    ) -> Reservation:
        assert during.lower is not None
        return Reservation(
            id=reservation_id,
            merchant=slug,
            starts_at=during.lower.astimezone(self.tz),
            party_size=party_size,
            table_label=table_label,
            status=status,
            guest_name=guest_name,
            via=via,
        )


def _not_found(reservation_id: str) -> NoReturn:
    raise NotFound(f"No booking {reservation_id!r} under that name")


def _hhmm(start: datetime, tz: ZoneInfo) -> str:
    return f"{start.astimezone(tz):%Y-%m-%d %H:%M}"


def _parse_hhmm(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError:
        raise InvalidInput(f"Time must be HH:MM, got {value!r}") from None
