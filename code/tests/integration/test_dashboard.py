"""Home's data (#135) on a real Postgres 16: the activity feed and the dashboard's blocks."""

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import demo_management
import pytest
import seed
from sqlalchemy import event, insert, select
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.management import ManagementService
from agent.service import PgAgentService
from core.db import make_engine, merchant_activity_t, merchant_conn, reservations_t, tables_t
from core.errors import InvalidInput, SlotUnavailable
from core.models import Dashboard, MerchantBookingInput, MoveBookingInput, Service, TableSpec

pytestmark = pytest.mark.integration

MerchantFactory = Callable[..., Awaitable[str]]  # the `make_merchant` fixture

TZ = ZoneInfo("Europe/Zurich")
TODAY = date(2026, 11, 2)  # Le Petit Zinc: 12:00-14:00, 19:00-22:00
NOW = datetime.combine(TODAY, time(19, 40), TZ)


def at(hhmm: str, day: date = TODAY) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), TZ)


def services(engine: AsyncEngine, now: datetime = NOW) -> ManagementService:
    agent = PgAgentService(engine, tz=TZ, clock=lambda: now)
    return ManagementService(engine, agent, tz=TZ, clock=lambda: now)


async def lines(engine: AsyncEngine, slug: str) -> list[tuple[str, str, str]]:
    """(kind, source, text) of the merchant's feed, oldest first."""
    feed = (await services(engine).dashboard(slug)).activity
    return [(item.kind, item.source, item.text) for item in reversed(feed)]


async def count_lines(engine: AsyncEngine, slug: str) -> int:
    async with engine.connect() as conn:
        rows = await conn.execute(
            select(merchant_activity_t.c.id).where(merchant_activity_t.c.merchant_slug == slug)
        )
        return len(rows.all())


async def past_booking(
    engine: AsyncEngine,
    slug: str,
    start: datetime,
    *,
    party: int = 2,
    via: str = "claude",
    status: str = "confirmed",
    label: str = "T1",
) -> None:
    """A booking written directly, as the seed does for days the service would refuse."""
    async with merchant_conn(engine, slug) as conn:
        table_id = await conn.scalar(select(tables_t.c.id).where(tables_t.c.label == label))
        await conn.execute(
            insert(reservations_t).values(
                id=f"H-{uuid4().hex[:8]}",
                table_id=table_id,
                guest_name="Past Guest",
                party_size=party,
                during=Range(start, start + timedelta(hours=2)),
                via=via,
                status=status,
                cancelled_by="guest" if status == "cancelled" else None,
            )
        )


async def test_every_path_writes_its_line_worded_at_read_time(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    early = services(engine, now=at("09:00"))
    agent = early.agent
    tomorrow = TODAY + timedelta(days=1)

    guest = await agent.create_reservation(
        slug, at("20:00", tomorrow), 2, "Sara Muller", "k-1", via="claude"
    )
    await agent.update_reservation(
        slug, guest.id, "Sara Muller", "k-2", starts_at=at("20:30", tomorrow)
    )
    own = await early.add(
        slug,
        MerchantBookingInput(
            starts_at=at("19:00"), party_size=4, guest_name="Lou", idempotency_key="k-3"
        ),
    )
    await early.move(slug, own.id, MoveBookingInput(starts_at=at("21:00"), idempotency_key="k-4"))
    await early.cancel(slug, own.id)
    await agent.cancel_reservation(slug, guest.id, by="guest", guest_name="Sara Muller")
    await agent.block_slot(slug, at("13:00", tomorrow))
    await agent.unblock_slot(slug, at("13:00", tomorrow))

    assert await count_lines(engine, slug) == 8  # Home shows the latest 6
    assert await lines(engine, slug) == [
        ("new_booking", "merchant", f"{own.id}, Lou, 4 guests, today 19:00"),
        ("moved", "merchant", f"{own.id}, Lou, 4 guests, today 21:00"),
        ("cancelled", "merchant", f"{own.id}, Lou, 4 guests, today 21:00"),
        ("cancelled", "claude", f"{guest.id}, Sara Muller, 2 guests, tomorrow 20:30"),
        ("blocked", "merchant", "tomorrow 13:00"),
        ("reopened", "merchant", "tomorrow 13:00"),
    ]
    next_day = services(engine, now=at("09:00", tomorrow))
    feed = (await next_day.dashboard(slug)).activity
    assert feed[-1].text == f"{own.id}, Lou, 4 guests, Mon 2 Nov 19:00"  # no stale "today"


async def test_settings_and_pausing_write_their_lines(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    agent = services(engine).agent

    await agent.update_policy(slug, max_party_size=4)
    await agent.update_policy(slug, services=[Service(start="18:00", end="22:00")])
    plan = [TableSpec(label=f"T{n}", seats=4 if n > 4 else 2) for n in range(1, 13)]
    await agent.update_tables(slug, plan)
    await agent.set_status(slug, "down")
    await agent.set_status(slug, "live")

    assert [(kind, text) for kind, _, text in await lines(engine, slug)] == [
        ("saved", "Booking rules"),
        ("saved", "Opening hours"),
        ("saved", "Floor plan"),
        ("paused", "Guests get your booking page."),
        ("resumed", "Your agent takes bookings again."),
    ]


async def test_a_failed_change_writes_no_line(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    building = await make_merchant(live=False)
    early = services(engine, now=at("09:00"))
    agent = early.agent
    for n in range(2):  # both 6-seat tables at 20:00
        await early.add(
            slug,
            MerchantBookingInput(
                starts_at=at("20:00"), party_size=6, guest_name=f"G{n}", idempotency_key=f"g-{n}"
            ),
        )
    before = await count_lines(engine, slug)

    with pytest.raises(SlotUnavailable):
        await early.add(
            slug,
            MerchantBookingInput(
                starts_at=at("20:00"), party_size=6, guest_name="G2", idempotency_key="g-2"
            ),
        )
    with pytest.raises(InvalidInput):
        await agent.block_slot(slug, at("13:15"))  # not a start time
    with pytest.raises(InvalidInput):
        await agent.set_status(building, "down")  # still building
    await agent.block_slot(slug, at("13:00"))
    await agent.block_slot(slug, at("13:00"))  # already blocked: nothing changed

    assert await count_lines(engine, slug) == before + 1
    assert await count_lines(engine, building) == 0


async def test_the_dashboard_counts_the_30_days_the_next_7_and_the_channels(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    ten_days_ago = TODAY - timedelta(days=10)
    await past_booking(engine, slug, at("20:00", ten_days_ago), party=4, label="T5")
    await past_booking(engine, slug, at("20:00", ten_days_ago), party=2, via="merchant")
    await past_booking(engine, slug, at("12:00", ten_days_ago), status="cancelled")
    await past_booking(engine, slug, at("20:00", TODAY - timedelta(days=30)))  # outside
    manage = services(engine)
    await manage.agent.create_reservation(slug, at("20:00"), 3, "Kim Weber", "k-1", via="website")
    await manage.agent.create_reservation(
        slug, at("20:00", TODAY + timedelta(days=6)), 2, "Ada", "k-2", via="claude"
    )
    await manage.agent.create_reservation(
        slug, at("20:00", TODAY + timedelta(days=7)), 2, "Bea", "k-3", via="claude"
    )  # outside

    board = await manage.dashboard(slug)

    assert board.date == TODAY
    assert [d.date for d in board.history] == [TODAY - timedelta(days=29 - n) for n in range(30)]
    day = next(d for d in board.history if d.date == ten_days_ago)
    assert (day.bookings, day.guests, day.agent_guests, day.cancelled) == (2, 6, 4, 1)
    assert board.history[-1].guests == 3  # today, through the website: not an agent's
    assert board.history[-1].agent_guests == 0
    assert [d.guests for d in board.next_days] == [3, 0, 0, 0, 0, 0, 2]
    assert {c.via: c.bookings for c in board.channels} == {
        "claude": 1,
        "merchant": 1,
        "website": 1,
    }
    assert sum(c.bookings for c in board.channels) == sum(d.bookings for d in board.history)
    with pytest.raises(InvalidInput):
        await manage.dashboard(slug, TODAY + timedelta(days=1))


async def test_the_room_is_tonights_service(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    early = services(engine, now=at("09:00"))
    for n, hhmm in enumerate(("19:00", "19:30", "20:00")):
        await early.add(
            slug,
            MerchantBookingInput(
                starts_at=at(hhmm), party_size=4, guest_name=f"G{n}", idempotency_key=f"r-{n}"
            ),
        )
    await early.add(
        slug,
        MerchantBookingInput(
            starts_at=at("12:00"), party_size=2, guest_name="Lunch", idempotency_key="r-l"
        ),
    )
    await early.agent.block_slot(slug, at("20:00"))

    room = (await services(engine).dashboard(slug)).room  # 19:40

    assert (room.service.start, room.service.end) == ("19:00", "22:00")
    assert (room.guests, room.seats, room.largest_group, room.party_size) == (12, 44, 6, 4)
    assert sum(t.booked for t in room.tables) == 3
    assert room.next_free == "20:30"  # 20:00 is blocked; 4-seat tables are left
    after_dinner = (await services(engine, now=at("23:00")).dashboard(slug)).room
    assert after_dinner.service.start == "19:00"  # its last service once they are over
    assert after_dinner.next_free is None


@contextmanager
def statements(engine: AsyncEngine) -> Iterator[list[str]]:
    seen: list[str] = []

    def record(*args: Any) -> None:
        seen.append(args[2])

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)


async def test_the_dashboard_takes_one_query_per_block_whatever_the_history(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    manage = services(engine)
    with statements(engine) as empty:
        await manage.dashboard(slug)
    for days_ago in range(1, 30):
        await past_booking(engine, slug, at("20:00", TODAY - timedelta(days=days_ago)))

    with statements(engine) as full:
        await manage.dashboard(slug)

    assert len(full) == len(empty) <= 10


async def test_search_finds_bookings_of_the_next_7_days_by_name_or_reference(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    manage = services(engine)
    tomorrow = TODAY + timedelta(days=1)
    lena = await manage.agent.create_reservation(
        slug, at("20:00", tomorrow), 2, "Lena  Becker", "s-1", via="claude"
    )
    later = await manage.agent.create_reservation(
        slug, at("20:00", TODAY + timedelta(days=9)), 2, "Lena Becker", "s-2", via="claude"
    )
    gone = await manage.agent.create_reservation(
        slug, at("21:00", tomorrow), 2, "Anna Weiß", "s-3", via="claude"
    )
    await manage.cancel(slug, gone.id)

    by_name = await manage.search(slug, "LENA becker")
    by_ref = await manage.search(slug, lena.id.lower())
    folded = await manage.search(slug, "weiss")

    assert [r.id for r in by_name.reservations] == [lena.id]  # not the one in 9 days
    assert later.id not in {r.id for r in by_ref.reservations}
    assert [(r.id, r.status) for r in folded.reservations] == [(gone.id, "cancelled")]
    assert (await manage.search(slug, "   ")).reservations == ()


@pytest.fixture
async def scratch_engine(migrated_scratch_database_url: str) -> AsyncIterator[AsyncEngine]:
    """A database of its own: the session one may already have a La Graine Verte."""
    engine = make_engine(migrated_scratch_database_url)
    yield engine
    await engine.dispose()


async def test_the_dashboard_shows_the_mockups_numbers_after_the_demo_seed(
    scratch_engine: AsyncEngine,
) -> None:
    """#131's acceptance numbers, from the real dashboard: Home shows them after `make seed`."""
    engine = scratch_engine
    day = date(2026, 11, 14)  # not the fixture's day: every date moves
    now = datetime.combine(day, time(19, 40), TZ)
    await seed.seed_merchant(engine, seed.load_profile(demo_management.MERCHANT))
    await demo_management.seed_management(engine, day)
    manage = services(engine, now=now)

    def totals(board: Dashboard) -> tuple[int, int, int, int, int, int]:
        return (
            sum(d.guests for d in board.history),
            sum(d.agent_guests for d in board.history),
            sum(d.cancelled for d in board.history),
            board.room.guests,
            board.room.seats,
            sum(d.guests for d in board.next_days),
        )

    before = totals(await manage.dashboard(demo_management.MERCHANT))
    await manage.agent.create_reservation(
        demo_management.MERCHANT,
        datetime.combine(day, time(20), TZ),
        2,
        "Kim Weber",
        "kim",
        via="claude",
    )
    board = await manage.dashboard(demo_management.MERCHANT)

    assert before == (982, 597, 42, 17, 24, 146)
    assert totals(board) == (984, 599, 42, 19, 24, 148)
    assert board.activity[0].text == "R-056, Kim Weber, 2 guests, today 20:00"
