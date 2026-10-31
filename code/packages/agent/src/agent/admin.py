"""Merchant-side operations of `PgAgentService` (#9).

Reservations list, stats, slot blocking, policy and floor-plan changes, and merchant
cancellation with a guest notice. Only the platform assistant calls them, never Claude users
(AGENTS.md §8.2). Each function runs on a connection from `merchant_conn`, so whatever one
call changes, in the merchant's schema and in the registry, commits or rolls back together.

None of them moves a booking (`during`, `party_size`, `table_id`): the live events treat
every reservation update as the guest's (`merchant_platform/events.py`, #81). A merchant-side
move must also tell the listener who made it, or the merchant's chat will report it as the
guest's.
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Row, delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from core.availability import bookable_starts, is_bookable_start
from core.db import (
    blocked_slots_t,
    guest_notices_t,
    merchants_t,
    policy_t,
    reservations_t,
    tables_t,
)
from core.errors import InvalidInput, NotFound
from core.models import AdminReservationRow, FloorPlan, Policy, Service, Services, Stats, TableSpec

# Any row shape (SQLAlchemy 2.1 rows are variadic generics).
_Row = Row[*tuple[Any, ...]]

# Bounds for merchant input, well inside Postgres `integer`: a typo never reaches the DB.
MAX_SEATS = 1000

_SERVICES = TypeAdapter(Services)
_FLOOR_PLAN = TypeAdapter(FloorPlan)

# SQL expression for a booking's start (lower bound of `during`).
_start_of = func.lower(reservations_t.c.during)


def _day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """The merchant's local day, [00:00, next 00:00): a 23:30 booking stays on its day."""
    try:
        next_day = day + timedelta(days=1)
    except OverflowError:
        raise InvalidInput(f"Date out of range: {day}") from None
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    return start, datetime.combine(next_day, datetime.min.time(), tzinfo=tz)


def _hhmm(moment: datetime, tz: ZoneInfo) -> str:
    return f"{moment.astimezone(tz):%H:%M}"


async def list_reservations(
    conn: AsyncConnection, day: date, tz: ZoneInfo, *, include_cancelled: bool = False
) -> list[AdminReservationRow]:
    """The day's bookings by start time; cancelled ones only on request."""
    start, end = _day_bounds(day, tz)
    query = (
        select(
            reservations_t.c.id,
            _start_of.label("starts_at"),
            reservations_t.c.guest_name,
            reservations_t.c.party_size,
            tables_t.c.label,
            reservations_t.c.via,
            reservations_t.c.status,
        )
        .join(tables_t, tables_t.c.id == reservations_t.c.table_id)
        .where(_start_of >= start, _start_of < end)
        .order_by(_start_of, reservations_t.c.id)
    )
    if not include_cancelled:
        query = query.where(reservations_t.c.status == "confirmed")
    return [
        AdminReservationRow(
            id=row.id,
            time=_hhmm(row.starts_at, tz),
            guest_name=row.guest_name,
            party_size=row.party_size,
            table_label=row.label,
            via=row.via,
            status=row.status,
        )
        for row in await conn.execute(query)
    ]


async def get_stats(conn: AsyncConnection, day: date, tz: ZoneInfo) -> Stats:
    """Confirmed bookings and guests that day, total capacity, the day's blocked starts."""
    bookings = await list_reservations(conn, day, tz)
    seats = await conn.scalar(select(func.coalesce(func.sum(tables_t.c.seats), 0)))
    start, end = _day_bounds(day, tz)
    blocked = await conn.scalars(
        select(blocked_slots_t.c.starts_at)
        .where(blocked_slots_t.c.starts_at >= start, blocked_slots_t.c.starts_at < end)
        .order_by(blocked_slots_t.c.starts_at)
    )
    return Stats(
        bookings=len(bookings),
        guests=sum(row.party_size for row in bookings),
        seats=seats or 0,
        blocked=tuple(_hhmm(moment, tz) for moment in blocked),
    )


async def block_slot(
    conn: AsyncConnection, services: Sequence[Service], starts_at: datetime, tz: ZoneInfo
) -> None:
    """No new booking may start at `starts_at`. Existing bookings stay (AGENTS.md §6)."""
    if not is_bookable_start(services, starts_at, tz):
        local = starts_at.astimezone(tz)
        valid = ", ".join(_hhmm(s, tz) for s in bookable_starts(services, local.date(), tz))
        raise InvalidInput(
            f"{local:%H:%M} is not a start time on {local:%Y-%m-%d}; valid times: {valid or 'none'}"
        )
    await conn.execute(
        pg_insert(blocked_slots_t).values(starts_at=starts_at).on_conflict_do_nothing()
    )


async def unblock_slot(conn: AsyncConnection, starts_at: datetime) -> None:
    await conn.execute(delete(blocked_slots_t).where(blocked_slots_t.c.starts_at == starts_at))


async def set_status(conn: AsyncConnection, slug: str, status: Literal["live", "down"]) -> None:
    """Pause (`down`) or resume (`live`) a published agent. `down` sends other agents and Claude
    to the merchant's booking page; its website keeps booking. A `building` merchant isn't
    published yet: only `deploy` makes it `live`."""
    result = await conn.execute(
        update(merchants_t)
        .where(merchants_t.c.slug == slug, merchants_t.c.status.in_(("live", "down")))
        .values(status=status)
    )
    if result.rowcount == 0:
        raise InvalidInput("This agent isn't published yet: publish it first")


async def _update_profile(conn: AsyncConnection, slug: str, changes: dict[str, Any]) -> None:
    """Keep the registry's display copy of the profile in step with the merchant's rows."""
    # FOR NO KEY UPDATE: serializes profile writers without blocking the foreign-key checks
    # of guest notices inserted concurrently.
    profile = await conn.scalar(
        select(merchants_t.c.profile)
        .where(merchants_t.c.slug == slug)
        .with_for_update(key_share=True)
    )
    if profile is None:
        raise NotFound(f"Unknown merchant: {slug!r}")
    await conn.execute(
        update(merchants_t).where(merchants_t.c.slug == slug).values(profile={**profile, **changes})
    )


async def update_policy(
    conn: AsyncConnection,
    slug: str,
    max_party_size: int | None,
    services: Sequence[Service] | None,
) -> Policy:
    """Change the group limit and/or the services. Existing bookings are never cancelled."""
    if max_party_size is None and services is None:
        raise InvalidInput("Provide max_party_size or services")
    if max_party_size is not None and not 1 <= max_party_size <= MAX_SEATS:
        raise InvalidInput(
            f"max_party_size must be between 1 and {MAX_SEATS}, got {max_party_size}"
        )
    values: dict[str, Any] = {}
    if max_party_size is not None:
        values["max_party_size"] = max_party_size
    if services is not None:
        try:
            checked = _SERVICES.validate_python(tuple(services))
        except ValidationError as error:
            raise InvalidInput(f"Invalid services: {_reasons(error)}") from None
        values["services"] = [service.model_dump() for service in checked]
    row = (await conn.execute(update(policy_t).values(values).returning(policy_t))).one()
    await _update_profile(conn, slug, values)
    return Policy(
        max_party_size=row.max_party_size,
        slot_length_min=row.slot_length_min,
        services=tuple(Service.model_validate(s) for s in row.services),
    )


async def update_tables(
    conn: AsyncConnection, slug: str, tables: Sequence[TableSpec], now: datetime, tz: ZoneInfo
) -> list[TableSpec]:
    """Replace the floor plan, matching tables by label.

    Refused, with nothing changed, when it would strand a booking: a removed table that
    holds an upcoming booking, or a kept table shrunk below one. A removed table with past
    or cancelled bookings is refused too, as their history still points to it.
    """
    try:
        plan = list(_FLOOR_PLAN.validate_python(tuple(tables)))
    except ValidationError as error:
        raise InvalidInput(f"Invalid floor plan: {_reasons(error)}") from None
    for table in plan:
        if not table.label.isprintable() or table.seats > MAX_SEATS:
            raise InvalidInput(
                f"Invalid table {table.label!r}: printable label and at most {MAX_SEATS} seats"
            )
    wanted = {table.label: table.seats for table in plan}
    # Locking every table, in id order, queues concurrent bookings and floor-plan changes
    # until the new plan is in place. Bookings re-check seats under their own table lock.
    current = (
        await conn.execute(
            select(tables_t.c.id, tables_t.c.label, tables_t.c.seats)
            .order_by(tables_t.c.id)
            .with_for_update()
        )
    ).all()
    await _check_plan_change(conn, current, wanted, now, tz)

    by_label = {row.label: row for row in current}
    removed = [row.id for row in current if row.label not in wanted]
    if removed:
        await conn.execute(delete(tables_t).where(tables_t.c.id.in_(removed)))
    for label, seats in wanted.items():
        if label in by_label:
            if by_label[label].seats != seats:
                await conn.execute(
                    update(tables_t).where(tables_t.c.label == label).values(seats=seats)
                )
        else:
            await conn.execute(insert(tables_t).values(label=label, seats=seats))
    await _update_profile(
        conn,
        slug,
        {
            "tables": [table.model_dump() for table in plan],
            "seats": sum(table.seats for table in plan),
        },
    )
    return plan


async def _check_plan_change(
    conn: AsyncConnection,
    current: Sequence[_Row],
    wanted: dict[str, int],
    now: datetime,
    tz: ZoneInfo,
) -> None:
    labels = {row.id: row.label for row in current}
    # Only tables that lose seats can strand a booking.
    affected = [
        row.id for row in current if row.label not in wanted or wanted[row.label] < row.seats
    ]
    if not affected:
        return
    bookings = await conn.execute(
        select(
            reservations_t.c.id,
            reservations_t.c.table_id,
            reservations_t.c.party_size,
            reservations_t.c.status,
            _start_of.label("starts_at"),
            func.upper(reservations_t.c.during).label("ends_at"),
        )
        .where(reservations_t.c.table_id.in_(affected))
        .order_by(_start_of, reservations_t.c.id)
    )
    problems: list[str] = []
    history: set[str] = set()
    for booking in bookings:
        label = labels[booking.table_id]
        upcoming = booking.status == "confirmed" and booking.ends_at > now
        when = f"{booking.starts_at.astimezone(tz):%Y-%m-%d %H:%M}"
        if label not in wanted:
            if upcoming:
                problems.append(f"{label} holds {booking.id} ({when})")
            else:
                history.add(label)
        elif upcoming and booking.party_size > wanted[label]:
            problems.append(
                f"{label} would seat {wanted[label]}, but {booking.id} ({when}) is for "
                f"{booking.party_size}"
            )
    if problems:
        raise InvalidInput("The new floor plan strands bookings: " + "; ".join(problems))
    if history:
        raise InvalidInput(
            f"{', '.join(sorted(history))} cannot be removed: past bookings refer to it. "
            "Keep it in the floor plan."
        )


# What a merchant's cancellation did (#82): cancelled with a notice for the guest, cancelled
# without one since it is over, or nothing, since the guest or the merchant already cancelled it.
CancelOutcome = Literal["notified", "ended", "guest_cancelled", "already_cancelled"]


async def cancel_by_merchant(
    conn: AsyncConnection, slug: str, reservation_id: str, tz: ZoneInfo, now: datetime
) -> tuple[_Row, CancelOutcome]:
    """Cancel a booking and leave the guest a notice, in the same transaction.

    Cancelling an already cancelled booking changes nothing and adds no second notice; a
    booking that has already ended is cancelled without a notice. Returns the booking row
    (with its table label), as it is after the cancellation, and what was done.
    """
    wanted_id = reservation_id.strip().upper()
    if not wanted_id.isprintable():
        raise NotFound(f"No booking {reservation_id!r}")
    row = (
        await conn.execute(
            select(reservations_t, tables_t.c.label)
            .join(tables_t, tables_t.c.id == reservations_t.c.table_id)
            .where(reservations_t.c.id == wanted_id)
            .with_for_update(of=reservations_t)
        )
    ).one_or_none()
    if row is None:
        raise NotFound(f"No booking {reservation_id!r}")
    if row.status == "cancelled":
        return row, "guest_cancelled" if row.cancelled_by == "guest" else "already_cancelled"
    await conn.execute(
        update(reservations_t)
        .where(reservations_t.c.id == row.id)
        .values(status="cancelled", cancelled_by="merchant")
    )
    notified = row.during.upper > now
    if notified:
        await _notify_guest(conn, slug, row, tz)
    cancelled = (
        await conn.execute(
            select(reservations_t, tables_t.c.label)
            .join(tables_t, tables_t.c.id == reservations_t.c.table_id)
            .where(reservations_t.c.id == row.id)
        )
    ).one()
    return cancelled, "notified" if notified else "ended"


async def _notify_guest(conn: AsyncConnection, slug: str, row: _Row, tz: ZoneInfo) -> None:
    """Stored notice, shown on the guest's next `get_my_reservations` (AGENTS.md §13)."""
    merchant = (
        await conn.execute(
            select(merchants_t.c.name, merchants_t.c.website).where(merchants_t.c.slug == slug)
        )
    ).one()
    start = row.during.lower.astimezone(tz)
    await conn.execute(
        insert(guest_notices_t).values(
            guest_name=row.guest_name,
            merchant_slug=slug,
            reservation_id=row.id,
            message=(
                f"{merchant.name} cancelled your booking {row.id} on {start:%Y-%m-%d} at "
                f"{start:%H:%M}. Please contact them at {merchant.website} or book again."
            ),
        )
    )


def _reasons(error: ValidationError) -> str:
    return "; ".join(str(detail["msg"]) for detail in error.errors())
