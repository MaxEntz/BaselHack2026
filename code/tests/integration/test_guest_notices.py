"""A guest's lookups on a real Postgres 16: upcoming bookings across merchants, names
compared as `same_guest` does (#77), and the notices a merchant leaves them (#91)."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.guests import same_guest
from agent.notices import NOTICE_WINDOW
from agent.provisioning import set_status
from agent.service import PgAgentService
from core.db import guest_notices_t
from tests.integration.booking_helpers import (
    DAY,
    NOW,
    TZ,
    Clock,
    MerchantFactory,
    at,
    book,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def clock() -> Clock:
    return Clock(NOW)


@pytest.fixture
def service(engine: AsyncEngine, clock: Clock) -> PgAgentService:
    return PgAgentService(engine, tz=TZ, clock=clock)


# find_guest_reservations


async def test_guest_sees_upcoming_bookings_across_live_merchants(
    engine: AsyncEngine, service: PgAgentService, make_merchant: MerchantFactory, clock: Clock
) -> None:
    guest = f"Guest {uuid4().hex[:6]}"
    zinc = await make_merchant()
    other = await make_merchant(name="Chez Mimi")
    hidden = await make_merchant()
    tomorrow = await book(service, other, at("19:00", DAY + timedelta(days=1)), guest_name=guest)
    soon = await book(service, zinc, at("20:00"), guest_name=guest)
    await book(service, hidden, at("20:00"), guest_name=guest)
    await set_status(hidden, "down", engine=engine)
    cancelled = await book(service, zinc, at("21:00"), guest_name=guest)
    await service.cancel_reservation(zinc, cancelled.id, by="guest", guest_name=guest)
    await book(service, zinc, at("20:00"), guest_name="Someone Else")
    clock.now = at("19:00")  # bookings starting before now are no longer "upcoming"
    await book(service, zinc, at("19:30"), guest_name=guest)
    clock.now = at("19:45")

    reservations, notices = await service.find_guest_reservations(guest.upper())

    assert [r.id for r in reservations] == [soon.id, tomorrow.id]
    assert [r.merchant for r in reservations] == [zinc, other]
    assert notices == []


@pytest.mark.parametrize(
    ("booked_as", "looked_up_as"),
    [
        ("Anna Weiß", "anna weiss"),
        ("Anna Weiß", "Anna Weiß"),
        ("Anna\u00a0Weber", "Anna Weber"),
        ("Anna Weber", "anna\u00a0WEBER"),
    ],
)
async def test_every_name_that_can_book_finds_its_bookings(
    service: PgAgentService, make_merchant: MerchantFactory, booked_as: str, looked_up_as: str
) -> None:
    # One definition of a guest, `same_guest`, for booking and for lookups (#77).
    slug = await make_merchant()
    token = uuid4().hex[:6]
    booked = await book(service, slug, at("20:00"), guest_name=f"{booked_as} {token}")

    reservations, _ = await service.find_guest_reservations(f"{looked_up_as} {token}")

    assert [r.id for r in reservations] == [booked.id]
    assert same_guest(f"{looked_up_as} {token}", booked.guest_name)


async def test_a_merchant_cancellation_reaches_a_guest_named_weiss(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    guest = f"Anna Weiß {uuid4().hex[:6]}"
    booked = await book(service, slug, at("20:00"), guest_name=guest)
    await service.cancel_reservation(slug, booked.id, by="merchant")

    _, notices = await service.find_guest_reservations(guest.replace("ß", "ss").lower())

    assert len(notices) == 1
    assert booked.id in notices[0]


async def test_a_lookup_leaves_another_guest_s_notice_alone(
    engine: AsyncEngine, service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    # Only `same_guest` now picks the notices to mark: another guest's must stay undelivered.
    slug = await make_merchant()
    token = uuid4().hex[:6]
    for guest in (f"Anna Weiß {token}", f"Anna Weber {token}"):
        async with engine.begin() as conn:
            await conn.execute(
                insert(guest_notices_t).values(
                    guest_name=guest, merchant_slug=slug, reservation_id="R-001", message=guest
                )
            )

    _, notices = await service.find_guest_reservations(f"anna weiss {token}")

    assert notices == [f"Anna Weiß {token}"]
    async with engine.connect() as conn:
        other = await conn.scalar(
            select(guest_notices_t.c.delivered_at).where(
                guest_notices_t.c.guest_name == f"Anna Weber {token}"
            )
        )
    assert other is None


async def test_guest_notices_come_back_for_a_day_then_stop(
    engine: AsyncEngine, service: PgAgentService, make_merchant: MerchantFactory, clock: Clock
) -> None:
    # An answer lost in transit must not lose a notice: a later call within the window gets it.
    guest = f"Guest {uuid4().hex[:6]}"
    slug = await make_merchant()
    async with engine.begin() as conn:
        for message in ["First notice", "Second notice"]:
            await conn.execute(
                insert(guest_notices_t).values(
                    guest_name=guest, merchant_slug=slug, reservation_id="R-001", message=message
                )
            )

    _, first = await service.find_guest_reservations(f" {guest.lower()} ")
    clock.now = NOW + NOTICE_WINDOW - timedelta(minutes=1)
    _, within = await service.find_guest_reservations(guest)
    clock.now = NOW + NOTICE_WINDOW + timedelta(seconds=1)
    _, after = await service.find_guest_reservations(guest)

    assert first == ["First notice", "Second notice"]
    assert within == first
    assert after == []
    async with engine.connect() as conn:
        delivered = await conn.scalar(
            select(func.count())
            .select_from(guest_notices_t)
            .where(guest_notices_t.c.guest_name == guest, guest_notices_t.c.delivered_at == NOW)
        )
    assert delivered == 2


async def notice(engine: AsyncEngine, guest: str, slug: str, message: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            insert(guest_notices_t).values(
                guest_name=guest, merchant_slug=slug, reservation_id="R-001", message=message
            )
        )


async def test_each_notice_has_its_own_day(
    engine: AsyncEngine, service: PgAgentService, make_merchant: MerchantFactory, clock: Clock
) -> None:
    guest = f"Guest {uuid4().hex[:6]}"
    slug = await make_merchant()
    await notice(engine, guest, slug, "Old notice")
    await service.find_guest_reservations(guest)
    clock.now = NOW + timedelta(hours=12)
    await notice(engine, guest, slug, "New notice")
    await service.find_guest_reservations(guest)

    clock.now = NOW + NOTICE_WINDOW + timedelta(seconds=1)
    _, notices = await service.find_guest_reservations(guest)

    assert notices == ["New notice"]


async def test_the_day_is_24_real_hours_across_a_clock_change(
    engine: AsyncEngine, service: PgAgentService, make_merchant: MerchantFactory, clock: Clock
) -> None:
    # Clocks go back on 2026-10-25: 12:00 CEST to 11:30 CET the next day is 24 h 30 min.
    guest = f"Guest {uuid4().hex[:6]}"
    slug = await make_merchant()
    await notice(engine, guest, slug, "A notice")
    clock.now = datetime(2026, 10, 24, 12, tzinfo=TZ)
    await service.find_guest_reservations(guest)

    clock.now = datetime(2026, 10, 25, 11, 30, tzinfo=TZ)
    _, notices = await service.find_guest_reservations(guest)

    assert notices == []
