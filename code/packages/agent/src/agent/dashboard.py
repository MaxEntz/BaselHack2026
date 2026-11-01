"""Home's data (#135): 30 days of history, the next 7 days, channels, tonight's room, activity.

One query per block, never one per day: a dashboard opens in one round trip per block whatever
the merchant's history. Days are the merchant's local days (`timezone(tz, lower(during))`).
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any, get_args
from zoneinfo import ZoneInfo

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from agent import activity
from agent.merchant import Merchant
from agent.times import hhmm, midnight, parse_hhmm
from core.availability import BusyInterval, bookable_starts, pick_table
from core.db import (
    blocked_between,
    busy_between,
    reservation_confirmed,
    reservation_start,
    reservations_t,
)
from core.models import (
    ACTIVITY_LINES,
    ActivityItem,
    ChannelCount,
    DayGuests,
    DayHistory,
    Room,
    RoomTable,
    Service,
    Via,
)

HISTORY_DAYS = 30
NEXT_DAYS = 7
# The group "Next table for 4" is about, or the largest one when smaller.
ROOM_PARTY = 4
# Bookings "through an agent": every channel but the merchant itself and its website.
OWN_CHANNELS = ("merchant", "website")


def _local_day(tz: ZoneInfo) -> Any:
    return cast(func.timezone(str(tz), reservation_start), Date)


async def days(
    conn: AsyncConnection, today: date, tz: ZoneInfo
) -> tuple[list[DayHistory], list[DayGuests]]:
    """The 30 days ending `today` and the 7 starting it, in one query over both windows."""
    first = today - timedelta(days=HISTORY_DAYS - 1)
    end = today + timedelta(days=NEXT_DAYS)
    day = _local_day(tz).label("day")
    party = reservations_t.c.party_size
    agent = reservation_confirmed & reservations_t.c.via.not_in(OWN_CHANNELS)
    rows = await conn.execute(
        select(
            day,
            func.count().filter(reservation_confirmed).label("bookings"),
            func.coalesce(func.sum(party).filter(reservation_confirmed), 0).label("guests"),
            func.coalesce(func.sum(party).filter(agent), 0).label("agent_guests"),
            func.count().filter(reservations_t.c.status == "cancelled").label("cancelled"),
        )
        .where(reservation_start >= midnight(first, tz), reservation_start < midnight(end, tz))
        .group_by(day)
    )
    by_day = {row.day: row for row in rows}

    def history(d: date) -> DayHistory:
        row = by_day.get(d)
        if row is None:
            return DayHistory(date=d, bookings=0, guests=0, agent_guests=0, cancelled=0)
        return DayHistory(
            date=d,
            bookings=row.bookings,
            guests=row.guests,
            agent_guests=row.agent_guests,
            cancelled=row.cancelled,
        )

    past = [history(first + timedelta(days=n)) for n in range(HISTORY_DAYS)]
    ahead = [
        DayGuests(date=d, guests=history(d).guests)
        for d in (today + timedelta(days=n) for n in range(NEXT_DAYS))
    ]
    return past, ahead


async def channels(conn: AsyncConnection, today: date, tz: ZoneInfo) -> list[ChannelCount]:
    """Live bookings per channel over the 30 days ending `today`: they add up to the history's
    bookings, so the percentage and its caption agree (#135)."""
    first = today - timedelta(days=HISTORY_DAYS - 1)
    rows = await conn.execute(
        select(reservations_t.c.via, func.count().label("bookings"))
        .where(
            reservation_confirmed,
            reservation_start >= midnight(first, tz),
            reservation_start < midnight(today + timedelta(days=1), tz),
        )
        .group_by(reservations_t.c.via)
    )
    counts = {row.via: row.bookings for row in rows}
    return [ChannelCount(via=via, bookings=counts[via]) for via in get_args(Via) if counts.get(via)]


async def room(conn: AsyncConnection, merchant: Merchant, *, now: datetime, tz: ZoneInfo) -> Room:
    """Today's current or next service, else its last one."""
    local = now.astimezone(tz)
    today = local.date()
    service = _service_of(merchant.services, local, tz)
    opens, closes = _window(service, today, tz)
    busy = await busy_between(conn, midnight(today, tz), closes + merchant.slot_length)
    in_service = [b for b in busy if opens <= b.start < closes]
    booked = {b.table_id for b in in_service}
    party_size = min(ROOM_PARTY, merchant.largest_group)
    return Room(
        service=service,
        seats=sum(t.seats for t in merchant.tables),
        guests=sum(b.party_size for b in in_service),
        tables=tuple(
            RoomTable(label=t.label, seats=t.seats, booked=t.id in booked)
            for t in sorted(merchant.tables, key=lambda t: t.id)
        ),
        party_size=party_size,
        next_free=await _next_free(conn, merchant, service, party_size, busy, now=now, tz=tz),
        largest_group=merchant.largest_group,
    )


def _service_of(services: Sequence[Service], local: datetime, tz: ZoneInfo) -> Service:
    """The service on now or next today, else the day's last (services are by start)."""
    today = local.date()
    return next(
        (s for s in services if datetime.combine(today, parse_hhmm(s.end), tz) > local),
        services[-1],
    )


def _window(service: Service, day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """When the service opens and closes on `day`, local time."""
    return (
        datetime.combine(day, parse_hhmm(service.start), tz),
        datetime.combine(day, parse_hhmm(service.end), tz),
    )


async def _next_free(
    conn: AsyncConnection,
    merchant: Merchant,
    service: Service,
    party_size: int,
    busy: Sequence[BusyInterval],
    *,
    now: datetime,
    tz: ZoneInfo,
) -> str | None:
    """The service's first start still to come that isn't blocked and has a table for
    `party_size`, which never exceeds the merchant's limit (`Merchant.largest_group`)."""
    today = now.astimezone(tz).date()
    opens, closes = _window(service, today, tz)
    blocked = set(await blocked_between(conn, opens, closes))
    return next(
        (
            hhmm(start, tz)
            for start in bookable_starts([service], today, tz)
            if start > now
            and start not in blocked
            and pick_table(merchant.tables, party_size, start, merchant.slot_length, busy)
            is not None
        ),
        None,
    )


async def recent(conn: AsyncConnection, slug: str, today: date, tz: ZoneInfo) -> list[ActivityItem]:
    return await activity.latest(conn, slug, today=today, tz=tz, limit=ACTIVITY_LINES)
