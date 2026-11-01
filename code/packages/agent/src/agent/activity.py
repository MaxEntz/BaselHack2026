"""What changed at a merchant (#135): one row per change, worded when Home reads it.

Every write path records its change on the connection of its own transaction, so the line and
the change commit or roll back together. Rows keep what the line is worded from (`details`),
not the words: "today 18:30" must read "Sat 31 Oct 18:30" the next day.
"""

import logging
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.words import short_day
from core.db import merchant_activity_t
from core.models import ActivityItem, ActivityKind, Via

log = logging.getLogger("agent.activity")

# Who a guest's own change comes from: an agent, since the website only books (#135).
GUEST_SOURCE: Via = "claude"
# What a "saved" line names (`details["what"]`).
OPENING_HOURS = "Opening hours"
BOOKING_RULES = "Booking rules"
FLOOR_PLAN = "Floor plan"
SETTINGS = "Settings"
PAUSED = "Guests get your booking page."
RESUMED = "Your agent takes bookings again."


async def record(
    conn: AsyncConnection,
    slug: str,
    kind: ActivityKind,
    source: Via,
    *,
    reservation_id: str | None = None,
    details: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> None:
    """`at` is the operation's clock (the service's, frozen in tests and demos); the
    database's own time without it."""
    values: dict[str, Any] = {
        "merchant_slug": slug,
        "kind": kind,
        "source": source,
        "reservation_id": reservation_id,
        "details": details or {},
    }
    if at is not None:
        values["at"] = at
    await conn.execute(insert(merchant_activity_t).values(values))


async def record_booking(
    conn: AsyncConnection,
    slug: str,
    kind: ActivityKind,
    source: Via,
    reservation_id: str,
    guest_name: str,
    party_size: int,
    starts_at: datetime,
    *,
    at: datetime | None = None,
) -> None:
    """A booking's line: new, moved or cancelled, with the booking as it then stood."""
    details = {"guest": guest_name, "party": party_size, "starts_at": starts_at.isoformat()}
    await record(conn, slug, kind, source, reservation_id=reservation_id, details=details, at=at)


async def record_times(
    conn: AsyncConnection,
    slug: str,
    kind: ActivityKind,
    times: Sequence[datetime],
    *,
    at: datetime | None = None,
) -> None:
    """Blocked or reopened start times, one line per change."""
    details = {"times": [moment.isoformat() for moment in sorted(times)]}
    await record(conn, slug, kind, "merchant", details=details, at=at)


async def latest(
    conn: AsyncConnection, slug: str, *, today: date, tz: ZoneInfo, limit: int
) -> list[ActivityItem]:
    """The newest lines first, worded relative to `today`."""
    columns = merchant_activity_t.c
    rows = await conn.execute(
        select(
            columns.id,
            columns.at,
            columns.kind,
            columns.source,
            columns.reservation_id,
            columns.details,
        )
        .where(columns.merchant_slug == slug)
        .order_by(columns.at.desc(), columns.id.desc())
        .limit(limit)
    )
    return [
        ActivityItem(
            id=row.id,
            at=row.at.astimezone(tz),
            kind=row.kind,
            text=word(row.kind, row.reservation_id, row.details, today, tz),
            source=row.source,
            reservation_id=row.reservation_id,
        )
        for row in rows
    ]


def word(
    kind: ActivityKind,
    reservation_id: str | None,
    details: dict[str, Any],
    today: date,
    tz: ZoneInfo,
) -> str:
    """The line Home shows: "R-009, Sara Muller, 2 guests, today 18:30".

    Never fails: a line without its details (an older writer, a hand-made row) reads as its
    reference, so one bad row can't take Home down.
    """
    try:
        return _word(kind, reservation_id, details, today, tz)
    except (KeyError, TypeError, ValueError) as error:
        # The kind, the reference and the error's type only: the details may hold a guest's
        # name (§12), and Home reads the row again on every visit, so no traceback each time.
        log.warning(
            "activity line without its details",
            extra={"kind": kind, "reservation": reservation_id, "error": type(error).__name__},
        )
        return reservation_id or ""


def _word(
    kind: ActivityKind,
    reservation_id: str | None,
    details: dict[str, Any],
    today: date,
    tz: ZoneInfo,
) -> str:
    if kind in ("new_booking", "moved", "cancelled"):
        start = datetime.fromisoformat(details["starts_at"]).astimezone(tz)
        party = int(details["party"])
        guests = "1 guest" if party == 1 else f"{party} guests"
        return f"{reservation_id}, {details['guest']}, {guests}, {_when(start, today)}"
    if kind in ("blocked", "reopened"):
        times = [datetime.fromisoformat(t).astimezone(tz) for t in details["times"]]
        if not times:
            return ""
        days = sorted({moment.date() for moment in times})
        return "; ".join(
            f"{_day(day, today)} "
            + ", ".join(f"{moment:%H:%M}" for moment in times if moment.date() == day)
            for day in days
        )
    if kind == "saved":
        return str(details.get("what", ""))
    return PAUSED if kind == "paused" else RESUMED


def _when(moment: datetime, today: date) -> str:
    return f"{_day(moment.date(), today)} {moment:%H:%M}"


def _day(day: date, today: date) -> str:
    """ "today", "tomorrow", else "Tue 3 Nov", as the mock words it."""
    if day == today:
        return "today"
    if day == today + timedelta(days=1):
        return "tomorrow"
    return short_day(day)
