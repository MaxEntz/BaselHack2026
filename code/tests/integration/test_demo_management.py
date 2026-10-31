"""La Graine Verte's management data on a real Postgres (#131): Home's numbers, once."""

from collections.abc import AsyncIterator
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import demo_management
import pytest
import seed
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.service import PgAgentService
from core.db import (
    agent_requests_t,
    blocked_slots_t,
    make_engine,
    merchant_conn,
    reservations_t,
)
from core.errors import NotFound

ZURICH = ZoneInfo("Europe/Zurich")
TODAY = date(2026, 11, 14)  # not the fixture's day: every date moves
NOW = datetime.combine(TODAY, time(19, 40), ZURICH)


async def home(engine: AsyncEngine) -> tuple[int, int, int, int, int, int]:
    """30 days of guests, through the agent, cancelled; tonight's guests and bookings; guests
    over the 7 days from today, as the dashboard counts them."""
    async with merchant_conn(engine, demo_management.MERCHANT) as conn:
        rows = (
            await conn.execute(
                select(
                    reservations_t.c.party_size,
                    reservations_t.c.via,
                    reservations_t.c.status,
                    reservations_t.c.during,
                )
            )
        ).all()
    month, week, tonight, cancelled = [], [], [], 0
    for row in rows:
        start = row.during.lower.astimezone(ZURICH)
        in_month = TODAY - timedelta(days=29) <= start.date() <= TODAY
        if row.status == "cancelled":
            cancelled += in_month
            continue
        if in_month:
            month.append(row)
        if TODAY <= start.date() < TODAY + timedelta(days=7):
            week.append(row)
        if start.date() == TODAY and start.time() >= time(18, 30):
            tonight.append(row)
    return (
        sum(r.party_size for r in month),
        sum(r.party_size for r in month if r.via == "claude"),
        cancelled,
        sum(r.party_size for r in tonight),
        len(tonight),
        sum(r.party_size for r in week),
    )


@pytest.fixture
async def scratch_engine(migrated_scratch_database_url: str) -> AsyncIterator[AsyncEngine]:
    """A database of its own: the session one may already have a La Graine Verte."""
    engine = make_engine(migrated_scratch_database_url)
    yield engine
    await engine.dispose()


async def test_a_seed_shows_the_mockups_numbers_once_and_the_live_booking_is_r_056(
    scratch_engine: AsyncEngine,
) -> None:
    engine = scratch_engine
    with pytest.raises(NotFound):
        await demo_management.seed_management(engine, TODAY)
    await seed.seed_merchant(engine, seed.load_profile(demo_management.MERCHANT))

    first = await demo_management.seed_management(engine, TODAY)
    seeded = await home(engine)
    again = await demo_management.seed_management(engine, TODAY)

    assert first.startswith("management data: 55 bookings over the 7 days from 2026-11-14")
    assert first.endswith(", 24 agent requests")
    assert again == "management data kept"
    assert seeded == await home(engine) == (982, 597, 42, 17, 6, 146)
    async with merchant_conn(engine, demo_management.MERCHANT) as conn:
        blocked = list(await conn.scalars(select(blocked_slots_t.c.starts_at)))
    assert blocked == [datetime.combine(TODAY + timedelta(days=1), time(13), ZURICH)]
    async with engine.connect() as conn:
        requests = (
            await conn.execute(select(agent_requests_t).order_by(agent_requests_t.c.id))
        ).all()
    refused = [r for r in requests if r.status >= 400]
    assert len(requests) == 24
    assert [r.at for r in requests] == sorted(r.at for r in requests)
    assert [(r.at.astimezone(ZURICH).isoformat(), r.intent, r.status) for r in refused] == [
        ("2026-11-14T11:04:00+01:00", "AVAILABILITY", 413),
        ("2026-11-14T13:37:00+01:00", "BOOK", 409),
    ]
    assert refused[1].request["params"]["starts_at"] == "2026-11-14T20:30:00+01:00"

    agent = PgAgentService(engine, tz=ZURICH, clock=lambda: NOW)
    kim = await agent.create_reservation(
        demo_management.MERCHANT,
        datetime.combine(TODAY, time(20), ZURICH),
        2,
        "Kim Weber",
        idempotency_key="demo-booking-kim",
        via="claude",
    )

    assert (kim.id, kim.table_label) == ("R-056", "T4")
    assert await home(engine) == (984, 599, 42, 19, 7, 148)
