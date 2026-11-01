"""Guest notices (AGENTS.md §6, §13): what a guest is told when the merchant cancels or moves
their booking, returned by `get_my_reservations` (`PgAgentService.find_guest_reservations`).

Only for a booking a guest made, through an agent or the website (#134): the merchant who
entered a booking itself has nobody to tell. Stored per guest name in `public.guest_notices`,
in the transaction of the change; never pushed to Claude.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.guests import same_guest
from agent.times import hhmm
from core.db import AnyRow, guest_notices_t, merchants_t

# A notice comes back on every lookup for this long after the first one that returned it, so
# that an answer lost on its way to the guest doesn't lose the notice (#91).
NOTICE_WINDOW = timedelta(hours=24)


async def notify_cancellation(
    conn: AsyncConnection, slug: str, booking: AnyRow, tz: ZoneInfo
) -> None:
    """The merchant cancelled the guest's `booking` (a `reservations` row)."""
    merchant = await _merchant(conn, slug)
    start = booking.during.lower.astimezone(tz)
    await _leave(
        conn,
        slug,
        booking.id,
        booking.guest_name,
        f"{merchant.name} cancelled your booking {booking.id} on {start:%Y-%m-%d} at "
        f"{hhmm(start)}. Please contact them at {merchant.website} or book again.",
    )


async def notify_move(
    conn: AsyncConnection,
    slug: str,
    reservation_id: str,
    guest_name: str,
    start: datetime,
    tz: ZoneInfo,
) -> None:
    """The merchant moved the guest's booking to `start` (#134): told like a cancellation, so
    that nobody comes at the old time."""
    merchant = await _merchant(conn, slug)
    local = start.astimezone(tz)
    await _leave(
        conn,
        slug,
        reservation_id,
        guest_name,
        f"{merchant.name} moved your booking {reservation_id} on {local:%Y-%m-%d} to "
        f"{hhmm(local)}.",
    )


async def returned_to(conn: AsyncConnection, guest_name: str, now: datetime) -> list[str]:
    """The guest's notices, oldest first: each from its first lookup until `NOTICE_WINDOW`
    later, then never again; `delivered_at` keeps the time of that first lookup.

    Names are compared in Python with `same_guest` on every live notice: fine for the POC's
    handful of notices (past that, store a normalized key at insert time).
    """
    live = await conn.execute(
        select(guest_notices_t.c.id, guest_notices_t.c.guest_name, guest_notices_t.c.message)
        .where(
            or_(
                guest_notices_t.c.delivered_at.is_(None),
                # In UTC: 24 real hours, also across a clock change.
                guest_notices_t.c.delivered_at > now.astimezone(UTC) - NOTICE_WINDOW,
            )
        )
        .order_by(guest_notices_t.c.id)
    )
    mine = [row for row in live if same_guest(guest_name, row.guest_name)]
    if mine:
        await conn.execute(
            update(guest_notices_t)
            .where(guest_notices_t.c.id.in_([row.id for row in mine]))
            .values(delivered_at=func.coalesce(guest_notices_t.c.delivered_at, now))
        )
    return [row.message for row in mine]


async def _merchant(conn: AsyncConnection, slug: str) -> AnyRow:
    return (
        await conn.execute(
            select(merchants_t.c.name, merchants_t.c.website).where(merchants_t.c.slug == slug)
        )
    ).one()


async def _leave(
    conn: AsyncConnection, slug: str, reservation_id: str, guest_name: str, message: str
) -> None:
    """Store a notice for the guest's next `get_my_reservations`."""
    await conn.execute(
        insert(guest_notices_t).values(
            guest_name=guest_name,
            merchant_slug=slug,
            reservation_id=reservation_id,
            message=message,
        )
    )
