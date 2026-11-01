"""Writing a booking on a table (#8), shared by guests' and the merchant's operations.

Double booking is prevented by the exclusion constraint of `reservations`: each candidate table
is tried in its own SAVEPOINT, and an exclusion violation (23P01) moves on to the next one.
Before writing, the attempt locks the table's row in `tables` and reads its seats again. That
lock does not prevent double booking (the constraint does), it only queues writers on the same
table: without it, concurrent writers wait on each other's uncommitted rows while checking the
constraint and deadlock (40P01). Rolling back the SAVEPOINT releases the lock, so a transaction
never holds one table while waiting for another.

Also the rules a write checks first: a start a new booking may take, a booking that hasn't
started, and an update's idempotency key (ADR-0013).
"""

from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql import Executable

from agent import times
from core.availability import TableRow, bookable_starts, is_bookable_start
from core.db import blocked_slots_t, reservation_changes_t, reservations_t, tables_t
from core.errors import BookingStarted, InvalidInput, SlotUnavailable
from core.models import Service

EXCLUSION_VIOLATION = "23P01"
UNIQUE_VIOLATION = "23505"
IDEMPOTENCY_CONSTRAINT = "reservations_idempotency_key_key"

Outcome = Literal["done", "taken", "key_taken"]


def candidate_tables(
    tables: Sequence[TableRow], party_size: int, current_id: int | None = None
) -> list[TableRow]:
    """Tables that seat the party, smallest first then by label; the current one first."""
    fitting = sorted((t for t in tables if t.seats >= party_size), key=lambda t: (t.seats, t.label))
    current = [t for t in fitting if t.id == current_id]
    return current + [t for t in fitting if t.id != current_id]


def not_a_start(given: str, day: date, valid: Sequence[str]) -> str:
    """The refusal of `given` (local `HH:MM` times) on `day`, naming the day's `valid` starts."""
    listed = ", ".join(valid) or "none"
    return f"{given} is not a start time on {day:%Y-%m-%d}; valid times: {listed}"


def off_the_grid(services: Sequence[Service], start: datetime, tz: ZoneInfo) -> str:
    """`not_a_start` for the instant `start`: its local time, its day and that day's starts."""
    local = start.astimezone(tz)
    valid = [times.hhmm(s, tz) for s in bookable_starts(services, local.date(), tz)]
    return not_a_start(times.hhmm(local), local.date(), valid)


def check_start(
    services: Sequence[Service], start: datetime, *, now: datetime, tz: ZoneInfo
) -> None:
    """Raise `SlotUnavailable` unless `start` is a future start time of a service."""
    if not is_bookable_start(services, start, tz):
        raise SlotUnavailable(off_the_grid(services, start, tz))
    if times.utc(start) <= times.utc(now):
        raise SlotUnavailable(f"{times.day_and_time(start, tz)} is in the past")


async def check_new_start(
    conn: AsyncConnection,
    services: Sequence[Service],
    start: datetime,
    *,
    now: datetime,
    tz: ZoneInfo,
    merchant_name: str | None = None,
) -> None:
    """Raise `SlotUnavailable` unless a new booking may start at `start`: a future start of a
    service that isn't blocked. A guest is told whose block it is (`merchant_name`); the
    merchant, on its own site, only the time."""
    check_start(services, start, now=now, tz=tz)
    blocked = await conn.scalar(
        select(blocked_slots_t.c.starts_at).where(blocked_slots_t.c.starts_at == start)
    )
    if blocked is None:
        return
    if merchant_name is not None:
        raise SlotUnavailable(f"{merchant_name} has blocked {times.day_and_time(start, tz)}")
    raise SlotUnavailable(f"{times.hhmm(start, tz)} is blocked")


def check_not_started(reservation_id: str, start: datetime, *, now: datetime) -> None:
    """A booking changes only before it starts: after, it's the merchant's to settle (#79)."""
    if times.utc(start) <= times.utc(now):
        raise BookingStarted(reservation_id)


def _sqlstate(error: DBAPIError) -> str | None:
    state: str | None = getattr(error.orig, "sqlstate", None)
    return state


def _constraint(error: DBAPIError) -> str | None:
    diag = getattr(error.orig, "diag", None)
    name: str | None = getattr(diag, "constraint_name", None)
    return name


class _Unfit(Exception):
    """The table was removed or shrunk below the party since the floor plan was read."""


async def attempt(
    conn: AsyncConnection, table_id: int, party_size: int, statement: Executable
) -> Outcome:
    """Write a booking on `table_id` in a SAVEPOINT, so a conflict leaves the transaction usable.

    `taken`: the table is booked over that range (exclusion violation), or a concurrent
    `update_tables` removed it or made it too small. `key_taken`: a concurrent request with
    the same idempotency key won. Anything else is raised.
    """
    try:
        async with conn.begin_nested():
            # Queue behind other writers on this table (see the module docstring), then
            # re-read its seats under the lock: the floor plan may have changed since.
            seats = await conn.scalar(
                select(tables_t.c.seats).where(tables_t.c.id == table_id).with_for_update()
            )
            if seats is None or seats < party_size:
                # Raised inside the SAVEPOINT, so its rollback releases the lock.
                raise _Unfit
            await conn.execute(statement)
    except _Unfit:
        return "taken"
    except DBAPIError as error:
        if _sqlstate(error) == EXCLUSION_VIOLATION:
            return "taken"
        if _sqlstate(error) == UNIQUE_VIOLATION and _constraint(error) == IDEMPOTENCY_CONSTRAINT:
            return "key_taken"
        raise
    return "done"


async def first_free(
    conn: AsyncConnection,
    tables: Sequence[TableRow],
    party_size: int,
    write: Callable[[TableRow], Executable],
) -> TableRow | None:
    """`write` the booking on the first of `tables`, in their order, that takes it: that table.

    Each try is an `attempt`. None when every table was taken, or when a concurrent request with
    the same idempotency key won (`key_taken`, which only an insert can meet): the caller then
    returns that request's booking, or refuses.
    """
    for table in tables:
        outcome = await attempt(conn, table.id, party_size, write(table))
        if outcome == "done":
            return table
        if outcome == "key_taken":
            return None
    return None


def moved_to(
    reservation_id: str, span: Range[datetime], party_size: int | None = None
) -> Callable[[TableRow], Executable]:
    """`first_free`'s write for a booking that moves to `span`, on the table tried, with its new
    `party_size` when given: a guest's update, or the merchant's move."""

    def write(table: TableRow) -> Executable:
        values: dict[str, Any] = {"table_id": table.id, "during": span}
        if party_size is not None:
            values["party_size"] = party_size
        return update(reservations_t).where(reservations_t.c.id == reservation_id).values(values)

    return write


async def claim_update_key(
    conn: AsyncConnection,
    slug: str,
    key: str,
    reservation_id: str,
    starts_at: datetime | None,
    party_size: int | None,
) -> bool:
    """True if this update is new: its key is then stored, in the update's transaction.

    A failed update rolls the key back with it. False if the key already applied this very
    change; another change under the same key is `InvalidInput`, as for creations. Guests'
    updates and the merchant's moves share one namespace (ADR-0013). The booking's row lock,
    which the caller takes first, queues two concurrent uses of one key.
    """
    claimed = await conn.scalar(
        pg_insert(reservation_changes_t)
        .values(
            merchant_slug=slug,
            idempotency_key=key,
            reservation_id=reservation_id,
            starts_at=starts_at,
            party_size=party_size,
        )
        .on_conflict_do_nothing(index_elements=["merchant_slug", "idempotency_key"])
        .returning(reservation_changes_t.c.idempotency_key)
    )
    if claimed is not None:
        return True
    applied = (
        await conn.execute(
            select(
                reservation_changes_t.c.reservation_id,
                reservation_changes_t.c.starts_at,
                reservation_changes_t.c.party_size,
            ).where(
                reservation_changes_t.c.merchant_slug == slug,
                reservation_changes_t.c.idempotency_key == key,
            )
        )
    ).one()
    stored = (applied.reservation_id, times.utc(applied.starts_at), applied.party_size)
    if stored != (reservation_id, times.utc(starts_at), party_size):
        raise InvalidInput("idempotency_key reused with different parameters")
    return False
