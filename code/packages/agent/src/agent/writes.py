"""Writing a booking on a table (#8), shared by guests' and the merchant's operations.

Each candidate table is tried in its own SAVEPOINT under the table's row lock, with its seats
read again under that lock; the exclusion constraint is what prevents double booking.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql import Executable

from core.availability import TableRow, bookable_starts, is_bookable_start
from core.db import tables_t
from core.errors import SlotUnavailable
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
    if start.astimezone(UTC) <= now.astimezone(UTC):
        raise SlotUnavailable(f"{start.astimezone(tz):%Y-%m-%d %H:%M} is in the past")


def _sqlstate(error: DBAPIError) -> str | None:
    state: str | None = getattr(error.orig, "sqlstate", None)
    return state


def _constraint(error: DBAPIError) -> str | None:
    diag = getattr(error.orig, "diag", None)
    name: str | None = getattr(diag, "constraint_name", None)
    return name


class _Unfit(Exception):
    """The table was removed or shrunk below the party since `_load` read the floor plan."""


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
            # re-read its seats under the lock: the floor plan may have changed since `_load`.
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
