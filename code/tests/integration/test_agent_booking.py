"""Claude-side bookings on a real Postgres 16: the exclusion constraint does the locking.

A guest's updates are in test_agent_updates.py, their lookups and notices in
test_guest_notices.py; the helpers all three share are in booking_helpers.py.
"""

import asyncio
import logging
from datetime import date, datetime, time, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.provisioning import set_status
from agent.service import PgAgentService
from core.db import blocked_slots_t, merchant_conn, reservations_t
from core.errors import InvalidInput, NotFound, PartyTooLarge, SlotUnavailable
from core.models import Reservation
from tests.integration.booking_helpers import (
    DAY,
    DINNER,
    GUEST,
    NOW,
    ONE_TABLE,
    ROUNDS,
    TWO_TABLES,
    TZ,
    Clock,
    MerchantFactory,
    at,
    book,
    confirmed,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def clock() -> Clock:
    return Clock(NOW)


@pytest.fixture
def service(engine: AsyncEngine, clock: Clock) -> PgAgentService:
    return PgAgentService(engine, tz=TZ, clock=clock)


# get_availability


async def test_availability_offers_the_closest_starts_with_their_table(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()

    slots = await service.get_availability(slug, DAY, 3, "20:00")

    assert [(slot.starts_at, slot.table_label) for slot in slots] == [
        (at("19:30"), "T10"),
        (at("20:00"), "T10"),
        (at("20:30"), "T10"),
    ]


async def test_availability_skips_booked_and_blocked_starts(
    engine: AsyncEngine, service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(tables=ONE_TABLE, services=DINNER)
    await book(service, slug, at("19:00"))
    async with merchant_conn(engine, slug) as conn:
        await conn.execute(insert(blocked_slots_t).values(starts_at=at("21:00")))

    slots = await service.get_availability(slug, DAY, 2)

    # 19:00-21:00 is taken and 21:00 is blocked: nothing left on the only table.
    assert slots == []


async def test_availability_without_a_time_starts_at_the_first_service(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()

    slots = await service.get_availability(slug, DAY, 2)

    assert [slot.starts_at for slot in slots] == [at("12:00"), at("12:30"), at("13:00")]


async def test_availability_hides_starts_already_past(
    service: PgAgentService, make_merchant: MerchantFactory, clock: Clock
) -> None:
    slug = await make_merchant(services=DINNER)
    clock.now = at("20:10")

    slots = await service.get_availability(slug, DAY, 2)

    assert [slot.starts_at for slot in slots] == [at("20:30"), at("21:00")]


async def test_availability_rejects_a_party_above_the_limit(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(max_party_size=6)

    with pytest.raises(PartyTooLarge, match="6"):
        await service.get_availability(slug, DAY, 7)


async def test_availability_rejects_a_party_no_table_can_seat(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    # Valid on purpose (CONTRACTS.md): the limit exceeds the largest table.
    slug = await make_merchant(tables=ONE_TABLE, max_party_size=8)

    with pytest.raises(PartyTooLarge):
        await service.get_availability(slug, DAY, 6)


async def test_availability_of_an_unknown_merchant(service: PgAgentService) -> None:
    with pytest.raises(NotFound):
        await service.get_availability("no-such-place", DAY, 2)


async def test_a_merchant_still_building_is_not_found(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(live=False)

    with pytest.raises(NotFound, match="not live yet"):
        await service.get_availability(slug, DAY, 2)
    with pytest.raises(NotFound, match="not live yet"):
        await book(service, slug, at("20:00"))


async def test_a_merchant_down_still_takes_bookings(
    engine: AsyncEngine, service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    # `down` stops Claude at the gateway (#13); the merchant's own site keeps working.
    slug = await make_merchant()
    await set_status(slug, "down", engine=engine)

    assert await service.get_availability(slug, DAY, 2)
    assert (await book(service, slug, at("20:00"))).status == "confirmed"


async def test_availability_rejects_an_invalid_time(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()

    with pytest.raises(InvalidInput):
        await service.get_availability(slug, DAY, 2, "8pm")


# create_reservation


async def test_a_booking_never_logs_the_guests_name(
    service: PgAgentService, make_merchant: MerchantFactory, caplog: pytest.LogCaptureFixture
) -> None:
    # §12, #220: not at INFO, and not in a debug line left over either.
    slug = await make_merchant()

    with caplog.at_level(logging.DEBUG):
        await book(service, slug, at("20:00"))

    assert [r for r in caplog.records if r.getMessage() == "reservation created"]
    assert all(GUEST not in str(record.__dict__) for record in caplog.records)


async def test_create_books_the_smallest_table_that_fits(
    engine: AsyncEngine, service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()

    reservation = await book(service, slug, at("20:00"), party_size=3)

    assert reservation == Reservation(
        id="R-001",
        merchant=slug,
        starts_at=at("20:00"),
        party_size=3,
        table_label="T10",  # T5-T10 seat 4; ties go by label as text (#5), so T10
        status="confirmed",
        guest_name=GUEST,
        via="claude",
    )
    assert reservation.starts_at.tzinfo == TZ
    assert len(await confirmed(engine, slug)) == 1


async def test_ten_concurrent_bookings_on_one_table_give_one_success(
    engine: AsyncEngine, service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(tables=ONE_TABLE, services=DINNER)

    for day_offset in range(ROUNDS):
        start = at("20:00", DAY + timedelta(days=day_offset))
        results = await asyncio.gather(
            *(book(service, slug, start) for _ in range(10)), return_exceptions=True
        )

        booked = [r for r in results if isinstance(r, Reservation)]
        assert len(booked) == 1, results
        assert all(isinstance(r, SlotUnavailable) for r in results if r not in booked), results
    assert len(await confirmed(engine, slug)) == ROUNDS


async def test_three_concurrent_bookings_on_two_tables_use_both(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(tables=TWO_TABLES, services=DINNER)

    for day_offset in range(ROUNDS):
        start = at("20:00", DAY + timedelta(days=day_offset))
        results = await asyncio.gather(
            *(book(service, slug, start) for _ in range(3)), return_exceptions=True
        )

        booked = [r for r in results if isinstance(r, Reservation)]
        assert sorted(r.table_label for r in booked) == ["T1", "T2"], results
        assert sum(isinstance(r, SlotUnavailable) for r in results) == 1


async def test_replaying_a_key_returns_the_same_booking(
    engine: AsyncEngine, service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()

    first = await book(service, slug, at("20:00"), key="claude-1")
    again = await book(service, slug, at("20:00"), key="claude-1")

    assert again == first
    assert len(await confirmed(engine, slug)) == 1


async def test_concurrent_replays_of_a_key_book_once(
    engine: AsyncEngine, service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(tables=ONE_TABLE, services=DINNER)

    results = await asyncio.gather(
        *(book(service, slug, at("20:00"), key="claude-retry") for _ in range(5))
    )

    assert len({r.id for r in results}) == 1
    assert len(await confirmed(engine, slug)) == 1


async def test_a_key_reused_for_another_booking_is_rejected(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    await book(service, slug, at("20:00"), key="claude-1")

    with pytest.raises(InvalidInput, match="idempotency_key"):
        await book(service, slug, at("20:30"), key="claude-1")


@pytest.mark.parametrize(
    ("hhmm", "reason"),
    [("20:15", "start time"), ("21:30", "start time"), ("08:00", "start time")],
)
async def test_create_rejects_a_start_off_the_grid(
    service: PgAgentService, make_merchant: MerchantFactory, hhmm: str, reason: str
) -> None:
    slug = await make_merchant(services=DINNER)

    with pytest.raises(SlotUnavailable, match=reason):
        await book(service, slug, at(hhmm))


async def test_create_rejects_a_past_start(
    service: PgAgentService, make_merchant: MerchantFactory, clock: Clock
) -> None:
    slug = await make_merchant()
    clock.now = at("20:00")

    with pytest.raises(SlotUnavailable, match="past"):
        await book(service, slug, at("20:00"))


async def test_create_rejects_a_blocked_start(
    engine: AsyncEngine, service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    async with merchant_conn(engine, slug) as conn:
        await conn.execute(insert(blocked_slots_t).values(starts_at=at("21:00")))

    with pytest.raises(SlotUnavailable, match="blocked"):
        await book(service, slug, at("21:00"))


async def test_create_rejects_a_party_above_the_limit(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(max_party_size=6)

    with pytest.raises(PartyTooLarge):
        await book(service, slug, at("20:00"), party_size=7)


async def test_bookings_back_to_back_on_one_table_both_succeed(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(tables=ONE_TABLE, services=[{"start": "19:00", "end": "23:00"}])

    first = await book(service, slug, at("19:00"))
    second = await book(service, slug, at("21:00"))

    assert first.table_label == second.table_label == "T1"


async def test_a_cancelled_booking_frees_its_table(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(tables=ONE_TABLE, services=DINNER)
    first = await book(service, slug, at("20:00"))
    await service.cancel_reservation(slug, first.id, by="guest", guest_name=GUEST)

    second = await book(service, slug, at("20:00"), guest_name="Alex Martin")

    assert second.table_label == "T1"


async def test_create_at_an_unknown_merchant(service: PgAgentService) -> None:
    with pytest.raises(NotFound):
        await book(service, "no-such-place", at("20:00"))


# cancel_reservation


async def test_cancel_matches_the_name_loosely_and_is_idempotent(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    reservation = await book(service, slug, at("20:00"))

    cancelled = await service.cancel_reservation(
        slug, reservation.id.lower(), by="guest", guest_name="  sam   DUPONT "
    )
    again = await service.cancel_reservation(slug, reservation.id, by="guest", guest_name=GUEST)

    assert cancelled.status == again.status == "cancelled"
    assert again == cancelled


async def test_cancel_under_another_name_changes_nothing(
    engine: AsyncEngine, service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    reservation = await book(service, slug, at("20:00"))

    with pytest.raises(NotFound):
        await service.cancel_reservation(slug, reservation.id, by="guest", guest_name="Alex Martin")

    assert [rid for rid, _ in await confirmed(engine, slug)] == [reservation.id]


async def test_cancel_of_an_unknown_booking(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()

    with pytest.raises(NotFound):
        await service.cancel_reservation(slug, "R-999", by="guest", guest_name=GUEST)


@pytest.mark.parametrize("now", ["20:00", "20:30", "23:00"])  # starting, started, over
async def test_a_guest_can_neither_move_nor_cancel_a_booking_once_it_started(
    engine: AsyncEngine,
    service: PgAgentService,
    make_merchant: MerchantFactory,
    clock: Clock,
    now: str,
) -> None:
    # Last night's dinner must not become tomorrow's booking, nor a cancellation (#79).
    slug = await make_merchant()
    booked = await book(service, slug, at("20:00"))
    clock.now = at(now)

    with pytest.raises(SlotUnavailable, match="already started"):
        await service.update_reservation(
            slug, booked.id, GUEST, str(uuid4()), starts_at=at("20:00", DAY + timedelta(days=1))
        )
    with pytest.raises(SlotUnavailable, match="already started"):
        await service.update_reservation(slug, booked.id, GUEST, str(uuid4()), party_size=3)
    with pytest.raises(SlotUnavailable, match="already started"):
        await service.cancel_reservation(slug, booked.id, by="guest", guest_name=GUEST)

    async with merchant_conn(engine, slug) as conn:
        row = (
            await conn.execute(
                select(reservations_t.c.status, reservations_t.c.during).where(
                    reservations_t.c.id == booked.id
                )
            )
        ).one()
    assert (row.status, row.during.lower) == ("confirmed", at("20:00"))


async def test_cancelling_an_already_cancelled_past_booking_stays_idempotent(
    service: PgAgentService, make_merchant: MerchantFactory, clock: Clock
) -> None:
    slug = await make_merchant()
    booked = await book(service, slug, at("20:00"))
    await service.cancel_reservation(slug, booked.id, by="guest", guest_name=GUEST)
    clock.now = at("23:00")

    again = await service.cancel_reservation(slug, booked.id, by="guest", guest_name=GUEST)

    assert again.status == "cancelled"


@pytest.mark.parametrize("day", [date(2026, 3, 29), date(2026, 10, 25)])
async def test_every_slot_offered_on_a_clock_change_night_can_be_booked(
    engine: AsyncEngine,
    service: PgAgentService,
    make_merchant: MerchantFactory,
    clock: Clock,
    day: date,
) -> None:
    # Availability and the database must agree on how long a slot lasts that night (#80).
    # Times go through ISO 8601, as from the gateway: a fixed offset, not a ZoneInfo.
    clock.now = datetime(2026, 3, 1, 9, tzinfo=TZ)
    slug = await make_merchant(tables=ONE_TABLE, services=[{"start": "00:00", "end": "04:00"}])
    await book(service, slug, datetime.combine(day, time(3), tzinfo=TZ))

    # Around 02:30: on 2026-10-25 that is the hour that repeats (02:00 and 02:30 CEST).
    slots = await service.get_availability(slug, day, 2, "02:30")

    assert slots
    for slot in slots:
        booked = await book(service, slug, datetime.fromisoformat(slot.starts_at.isoformat()))
        async with merchant_conn(engine, slug) as conn:
            during = await conn.scalar(
                select(reservations_t.c.during).where(reservations_t.c.id == booked.id)
            )
        assert during.upper - during.lower == timedelta(hours=2)
        await service.cancel_reservation(slug, booked.id, by="merchant")


async def test_a_booking_made_with_local_time_lasts_two_real_hours_on_a_clock_change(
    engine: AsyncEngine, service: PgAgentService, make_merchant: MerchantFactory, clock: Clock
) -> None:
    clock.now = datetime(2026, 3, 1, 9, tzinfo=TZ)
    slug = await make_merchant(services=[{"start": "00:00", "end": "04:00"}])

    booked = await book(service, slug, datetime.combine(date(2026, 3, 29), time(1), tzinfo=TZ))

    async with merchant_conn(engine, slug) as conn:
        during = await conn.scalar(
            select(reservations_t.c.during).where(reservations_t.c.id == booked.id)
        )
    assert during.upper - during.lower == timedelta(hours=2)
