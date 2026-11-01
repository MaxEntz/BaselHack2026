"""What the booking tests share (test_agent_booking, test_agent_updates, test_guest_notices):
the merchant's day and hours, a guest, a settable clock, and helpers to book and read a booking
back. Each test module declares its own `clock` and `service` fixtures from these."""

from collections.abc import Awaitable, Callable
from datetime import date, datetime, time
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.service import PgAgentService
from core.db import merchant_conn, reservations_t
from core.models import Reservation

MerchantFactory = Callable[..., Awaitable[str]]  # the `make_merchant` fixture

TZ = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 10, 31, 9, tzinfo=TZ)
DAY = date(2026, 11, 2)
GUEST = "Sam Dupont"
DINNER = [{"start": "19:00", "end": "22:00"}]
ONE_TABLE = [{"label": "T1", "seats": 4}]
TWO_TABLES = [{"label": "T1", "seats": 4}, {"label": "T2", "seats": 4}]
# AGENTS.md §11: the concurrency guarantees must hold run after run, not once.
ROUNDS = 20


def at(hhmm: str, day: date = DAY) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=TZ)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


async def book(
    service: PgAgentService,
    slug: str,
    start: datetime,
    party_size: int = 2,
    guest_name: str = GUEST,
    key: str | None = None,
) -> Reservation:
    return await service.create_reservation(
        slug, start, party_size, guest_name, key or str(uuid4()), via="claude"
    )


async def stored(engine: AsyncEngine, slug: str, reservation_id: str) -> tuple[datetime, int, str]:
    async with merchant_conn(engine, slug) as conn:
        row = (
            await conn.execute(
                select(
                    reservations_t.c.during, reservations_t.c.party_size, reservations_t.c.status
                ).where(reservations_t.c.id == reservation_id)
            )
        ).one()
    return row.during.lower, row.party_size, row.status


async def confirmed(engine: AsyncEngine, slug: str) -> list[tuple[str, int]]:
    async with merchant_conn(engine, slug) as conn:
        rows = await conn.execute(
            select(reservations_t.c.id, reservations_t.c.table_id)
            .where(reservations_t.c.status == "confirmed")
            .order_by(reservations_t.c.id)
        )
        return [(row.id, row.table_id) for row in rows]
