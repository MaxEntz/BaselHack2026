"""Merchant-side operations of `PgAgentService` (#9).

Reservations list, stats, guests, slot blocking, pausing the agent, the agent request log,
policy and floor-plan changes, and merchant cancellation with a guest notice. The management
site's other forms are `agent.site_forms`, the notices `agent.notices`. Only the platform
assistant and the management site call them, never Claude users (AGENTS.md §8.2). Each
function runs on a connection from `merchant_conn`, so whatever one call changes, in the
merchant's schema and in the registry, commits or rolls back together.

None of them moves a booking (`during`, `party_size`, `table_id`): the management site's moves
are `agent.management`'s, which marks its transaction as the merchant's (`core.db.as_merchant`,
#134) so the live events don't report them as the guest's (`merchant_platform/events.py`). A
cancellation here says who made it in the row (`cancelled_by`).
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Row, delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from agent import activity, notices
from agent.guests import guest_key
from agent.requests import describe
from agent.times import day_and_time, day_bounds, hhmm
from core.availability import bookable_starts, is_bookable_start
from core.db import (
    agent_requests_t,
    blocked_slots_t,
    merchants_t,
    policy_t,
    reservation_start,
    reservations_t,
    tables_t,
)
from core.errors import FormProblem, InvalidInput, NotFound
from core.forms import MAX_SEATS, party_limit_problem
from core.models import (
    ActivityKind,
    AdminReservationRow,
    AgentRequest,
    FloorPlan,
    GuestRow,
    MerchantCancelOutcome,
    Policy,
    Problem,
    Service,
    Services,
    Stats,
    TableSpec,
)

# Any row shape (SQLAlchemy 2.1 rows are variadic generics).
_Row = Row[*tuple[Any, ...]]

# What the Agent page shows of a day (`AgentRequests`).
MAX_AGENT_REQUESTS = 10

# The Guests page covers today and the 6 days after it.
DAYS_AHEAD = 7

_SERVICES = TypeAdapter(Services)
_FLOOR_PLAN = TypeAdapter(FloorPlan)


async def list_reservations(
    conn: AsyncConnection, day: date, tz: ZoneInfo, *, include_cancelled: bool = False
) -> list[AdminReservationRow]:
    """The day's bookings by start time; cancelled ones only on request."""
    start, end = day_bounds(day, tz)
    query = (
        select(
            reservations_t.c.id,
            reservation_start.label("starts_at"),
            reservations_t.c.guest_name,
            reservations_t.c.party_size,
            tables_t.c.label,
            reservations_t.c.via,
            reservations_t.c.status,
        )
        .join(tables_t, tables_t.c.id == reservations_t.c.table_id)
        .where(reservation_start >= start, reservation_start < end)
        .order_by(reservation_start, reservations_t.c.id)
    )
    if not include_cancelled:
        query = query.where(reservations_t.c.status == "confirmed")
    return [
        AdminReservationRow(
            id=row.id,
            time=hhmm(row.starts_at, tz),
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
    start, end = day_bounds(day, tz)
    blocked = await conn.scalars(
        select(blocked_slots_t.c.starts_at)
        .where(blocked_slots_t.c.starts_at >= start, blocked_slots_t.c.starts_at < end)
        .order_by(blocked_slots_t.c.starts_at)
    )
    return Stats(
        bookings=len(bookings),
        guests=sum(row.party_size for row in bookings),
        seats=seats or 0,
        blocked=tuple(hhmm(moment, tz) for moment in blocked),
    )


async def list_guests(
    conn: AsyncConnection, query: str, now: datetime, tz: ZoneInfo
) -> list[GuestRow]:
    """Guests with a live booking that hasn't started, from now to the end of the 7th day,
    by name (#144). One row per `guest_key`, kept if `query` is part of it."""
    _, end = day_bounds(now.astimezone(tz).date() + timedelta(days=DAYS_AHEAD - 1), tz)
    rows = await conn.execute(
        select(
            reservations_t.c.id,
            reservation_start.label("starts_at"),
            reservations_t.c.guest_name,
            reservations_t.c.party_size,
            reservations_t.c.via,
        )
        .where(
            reservations_t.c.status == "confirmed", reservation_start > now, reservation_start < end
        )
        .order_by(reservation_start, reservations_t.c.id)
    )
    wanted = guest_key(query)
    by_guest: dict[str, list[_Row]] = {}
    for row in rows:
        key = guest_key(row.guest_name)
        if wanted in key:
            by_guest.setdefault(key, []).append(row)
    return [
        GuestRow(
            guest_name=visits[0].guest_name,
            reservation_id=visits[0].id,
            date=visits[0].starts_at.astimezone(tz).date(),
            time=hhmm(visits[0].starts_at, tz),
            party_size=visits[0].party_size,
            bookings=len(visits),
            via=visits[0].via,
        )
        for _, visits in sorted(by_guest.items())
    ]


async def block_slot(
    conn: AsyncConnection,
    slug: str,
    services: Sequence[Service],
    starts_at: datetime,
    tz: ZoneInfo,
    *,
    at: datetime | None = None,
) -> None:
    """No new booking may start at `starts_at`. Existing bookings stay (AGENTS.md §6)."""
    if not is_bookable_start(services, starts_at, tz):
        local = starts_at.astimezone(tz)
        valid = ", ".join(hhmm(s, tz) for s in bookable_starts(services, local.date(), tz))
        raise InvalidInput(
            f"{local:%H:%M} is not a start time on {local:%Y-%m-%d}; valid times: {valid or 'none'}"
        )
    added = await conn.scalar(
        pg_insert(blocked_slots_t)
        .values(starts_at=starts_at)
        .on_conflict_do_nothing()
        .returning(blocked_slots_t.c.starts_at)
    )
    if added is not None:  # already blocked: nothing changed, no line
        await activity.record_times(conn, slug, "blocked", [starts_at], at=at)


async def unblock_slot(
    conn: AsyncConnection, slug: str, starts_at: datetime, *, at: datetime | None = None
) -> None:
    removed = await conn.scalar(
        delete(blocked_slots_t)
        .where(blocked_slots_t.c.starts_at == starts_at)
        .returning(blocked_slots_t.c.starts_at)
    )
    if removed is not None:
        await activity.record_times(conn, slug, "reopened", [starts_at], at=at)


async def pause_or_resume(
    conn: AsyncConnection,
    slug: str,
    status: Literal["live", "down"],
    *,
    at: datetime | None = None,
) -> None:
    """The merchant pauses (`down`) or resumes (`live`) its published agent (#146), with an
    activity line. `down` sends other agents and Claude to the merchant's booking page; its
    website keeps booking. A `building` merchant isn't published yet: only `deploy` makes it
    `live`. Unlike `provisioning.set_status`, the scripts' outage switch, which sets any
    provisioned merchant's status and records nothing."""
    current = await conn.scalar(
        select(merchants_t.c.status).where(merchants_t.c.slug == slug).with_for_update()
    )
    if current not in ("live", "down"):
        raise InvalidInput("This agent isn't published yet: publish it first")
    if current == status:
        return  # paused twice: nothing changed, no line (#135)
    await conn.execute(update(merchants_t).where(merchants_t.c.slug == slug).values(status=status))
    kind: ActivityKind = "paused" if status == "down" else "resumed"
    await activity.record(conn, slug, kind, "merchant", at=at)


async def list_agent_requests(
    conn: AsyncConnection,
    slug: str,
    day: date,
    tz: ZoneInfo,
    *,
    refused: bool,
    max_party_size: int,
) -> list[AgentRequest]:
    """The latest requests other agents sent on the merchant's local `day`, newest first,
    worded for the Agent page; `refused` keeps the ones answered with a status >= 400."""
    start, end = day_bounds(day, tz)
    t = agent_requests_t
    query = (
        select(t)
        .where(t.c.merchant_slug == slug, t.c.at >= start, t.c.at < end)
        .order_by(t.c.at.desc(), t.c.id.desc())
        .limit(MAX_AGENT_REQUESTS)
    )
    if refused:
        query = query.where(t.c.status >= 400)
    found = []
    for row in await conn.execute(query):
        title, text = describe(
            row.agent_name,
            row.intent,
            row.status,
            row.request,
            row.response,
            max_party_size=max_party_size,
            tz=tz,
        )
        found.append(
            AgentRequest(
                id=row.id,
                at=row.at.astimezone(tz),
                agent_name=row.agent_name,
                intent=row.intent,
                status=row.status,
                refused=row.status >= 400,
                title=title,
                text=text,
                request=row.request,
                response=row.response,
            )
        )
    return found


async def update_profile(conn: AsyncConnection, slug: str, changes: dict[str, Any]) -> None:
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


def check_party_limit(max_party_size: int) -> None:
    """The group limit, as the tools word its refusal; the form words it its own way
    (`core.forms.party_limit_problem`, the rule's one check)."""
    if party_limit_problem(max_party_size) is not None:
        raise InvalidInput(
            f"max_party_size must be between 1 and {MAX_SEATS}, got {max_party_size}"
        )


async def update_policy(
    conn: AsyncConnection,
    slug: str,
    max_party_size: int | None,
    services: Sequence[Service] | None,
    *,
    at: datetime | None = None,
) -> Policy:
    """Change the group limit and/or the services. Existing bookings are never cancelled."""
    if max_party_size is None and services is None:
        raise InvalidInput("Provide max_party_size or services")
    if max_party_size is not None:
        check_party_limit(max_party_size)
    values: dict[str, Any] = {}
    if max_party_size is not None:
        values["max_party_size"] = max_party_size
    if services is not None:
        try:
            checked = _SERVICES.validate_python(tuple(services))
        except ValidationError as error:
            raise InvalidInput(f"Invalid services: {_reasons(error)}") from None
        # In the order `get_policy` serves them: the same hours in another order are no change,
        # and the tool descriptions word them alike (#201).
        values["services"] = [s.model_dump() for s in sorted(checked, key=lambda s: s.start)]
    before = (await conn.execute(select(policy_t.c.max_party_size, policy_t.c.services))).one()
    row = (await conn.execute(update(policy_t).values(values).returning(policy_t))).one()
    await update_profile(conn, slug, values)
    # One line per section that changed: an identical save, or a retry, writes none (#135).
    if "max_party_size" in values and values["max_party_size"] != before.max_party_size:
        await activity.record(
            conn, slug, "saved", "merchant", details={"what": activity.BOOKING_RULES}, at=at
        )
    stored_services = sorted(before.services, key=lambda s: str(s["start"]))
    if "services" in values and values["services"] != stored_services:
        await activity.record(
            conn, slug, "saved", "merchant", details={"what": activity.OPENING_HOURS}, at=at
        )
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
    await update_profile(
        conn,
        slug,
        {
            "tables": [table.model_dump() for table in plan],
            "seats": sum(table.seats for table in plan),
        },
    )
    if sorted((t.label, t.seats) for t in plan) != sorted((t.label, t.seats) for t in current):
        await activity.record(
            conn, slug, "saved", "merchant", details={"what": activity.FLOOR_PLAN}, at=now
        )
    return plan


async def _check_plan_change(
    conn: AsyncConnection,
    current: Sequence[_Row],
    wanted: dict[str, int],
    now: datetime,
    tz: ZoneInfo,
) -> None:
    """Refuse a floor plan that strands a booking, with the first refusal as a `Problem`, so
    the management site can word it ("T3 has to stay, because Kim Weber's booking refers to
    it.", #133)."""
    labels = {row.id: row.label for row in current}
    # Only tables that lose seats can strand a booking.
    affected = [
        row.id for row in current if row.label not in wanted or wanted[row.label] < row.seats
    ]
    if not affected:
        return
    problems: list[tuple[str, Problem]] = []
    history: dict[str, str] = {}  # removed table -> guest of its first past booking
    for booking in await _bookings_on(conn, affected):
        label = labels[booking.table_id]
        if booking.status != "confirmed" or booking.ends_at <= now:
            if label not in wanted:
                history.setdefault(label, booking.guest_name)
        elif problem := _stranded(booking, label, wanted.get(label), tz):
            problems.append(problem)
    if problems:
        raise FormProblem(
            "The new floor plan strands bookings: " + "; ".join(text for text, _ in problems),
            problems[0][1],
        )
    if history:
        first = sorted(history)[0]
        raise FormProblem(
            f"{', '.join(sorted(history))} cannot be removed: past bookings refer to it. "
            "Keep it in the floor plan.",
            Problem(code="table_in_use", table=first, guest=history[first]),
        )


async def _bookings_on(conn: AsyncConnection, table_ids: Sequence[int]) -> Sequence[_Row]:
    """Every booking on these tables, past and cancelled included, by start."""
    rows = await conn.execute(
        select(
            reservations_t.c.id,
            reservations_t.c.table_id,
            reservations_t.c.guest_name,
            reservations_t.c.party_size,
            reservations_t.c.status,
            reservation_start.label("starts_at"),
            func.upper(reservations_t.c.during).label("ends_at"),
        )
        .where(reservations_t.c.table_id.in_(table_ids))
        .order_by(reservation_start, reservations_t.c.id)
    )
    return rows.all()


def _stranded(
    booking: _Row, label: str, seats: int | None, tz: ZoneInfo
) -> tuple[str, Problem] | None:
    """Why the new plan strands the upcoming `booking` on `label`, whose seats become `seats`
    (None: removed), if it does."""
    when = day_and_time(booking.starts_at, tz)
    guest = booking.guest_name
    if seats is None:
        return (
            f"{label} holds {booking.id} ({when})",
            Problem(code="table_in_use", table=label, guest=guest),
        )
    if booking.party_size > seats:
        return (
            f"{label} would seat {seats}, but {booking.id} ({when}) is for {booking.party_size}",
            Problem(code="table_too_small", table=label, guest=guest, seats=booking.party_size),
        )
    return None


async def cancel_by_merchant(
    conn: AsyncConnection, slug: str, reservation_id: str, tz: ZoneInfo, now: datetime
) -> tuple[_Row, MerchantCancelOutcome]:
    """Cancel a booking and leave the guest a notice, in the same transaction.

    Cancelling an already cancelled booking changes nothing and adds no second notice; a
    booking that has already ended, or that the merchant made itself, is cancelled without one.
    Returns the booking row (with its table label), as it is after the cancellation, and what
    was done (#82).
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
    await activity.record_booking(
        conn,
        slug,
        "cancelled",
        "merchant",
        row.id,
        row.guest_name,
        row.party_size,
        row.during.lower,
        at=now,
    )
    # A notice only for a booking a guest made, through an agent or the website (#134): the
    # merchant who entered a booking itself has nobody to tell.
    outcome: MerchantCancelOutcome
    if row.during.upper <= now:
        outcome = "ended"
    elif row.via == "merchant":
        outcome = "own_booking"
    else:
        outcome = "notified"
        await notices.notify_cancellation(conn, slug, row, tz)
    cancelled = (
        await conn.execute(
            select(reservations_t, tables_t.c.label)
            .join(tables_t, tables_t.c.id == reservations_t.c.table_id)
            .where(reservations_t.c.id == row.id)
        )
    ).one()
    return cancelled, outcome


def _reasons(error: ValidationError) -> str:
    return "; ".join(str(detail["msg"]) for detail in error.errors())
