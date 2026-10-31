"""Claude-side bookings on a real Postgres 16: the exclusion constraint does the locking."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.provisioning import set_status
from agent.service import NOTICE_WINDOW, PgAgentService, same_guest
from core.db import (
    blocked_slots_t,
    guest_notices_t,
    merchant_conn,
    reservation_changes_t,
    reservations_t,
)
from core.errors import InvalidInput, NotFound, PartyTooLarge, SlotUnavailable
from core.models import Reservation

pytestmark = pytest.mark.integration

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


@pytest.fixture
def clock() -> Clock:
    return Clock(NOW)


@pytest.fixture
def service(engine: AsyncEngine, clock: Clock) -> PgAgentService:
    return PgAgentService(engine, tz=TZ, clock=clock)


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


# update_reservation


async def test_update_moves_a_booking_and_keeps_its_table(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(tables=TWO_TABLES, services=DINNER)
    await book(service, slug, at("19:00"))  # T1
    moving = await book(service, slug, at("19:00"))  # T2
    # T1 is free again, but the current table comes first.
    await service.cancel_reservation(slug, "R-001", by="guest", guest_name=GUEST)

    moved = await service.update_reservation(
        slug, moving.id, GUEST, "move-1", starts_at=at("19:30")
    )

    assert (moved.id, moved.starts_at, moved.table_label) == (moving.id, at("19:30"), "T2")


async def test_update_moves_a_larger_party_to_a_larger_table(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    reservation = await book(service, slug, at("20:00"), party_size=2)

    updated = await service.update_reservation(slug, reservation.id, GUEST, "up-1", party_size=5)

    assert (updated.party_size, updated.table_label, updated.starts_at) == (5, "T11", at("20:00"))


async def test_update_to_a_full_slot_leaves_the_booking_unchanged(
    engine: AsyncEngine, service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(tables=ONE_TABLE, services=[{"start": "19:00", "end": "23:00"}])
    await book(service, slug, at("19:00"))
    later = await book(service, slug, at("21:00"))

    with pytest.raises(SlotUnavailable):
        await service.update_reservation(slug, later.id, GUEST, "move-1", starts_at=at("20:00"))

    assert await stored(engine, slug, later.id) == (at("21:00"), 2, "confirmed")


async def test_replaying_an_update_gives_the_same_state(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    reservation = await book(service, slug, at("20:00"))

    first = await service.update_reservation(
        slug, reservation.id, GUEST, "move-1", starts_at=at("21:00")
    )
    again = await service.update_reservation(
        slug, reservation.id, GUEST, "move-1", starts_at=at("21:00")
    )

    assert again == first


async def test_a_late_retry_of_an_update_does_not_undo_a_newer_one(
    engine: AsyncEngine, service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    # k1 moves to 20:00, k2 to 21:00, then k1 arrives again: the guest's newer request wins (#78).
    slug = await make_merchant()
    booked = await book(service, slug, at("19:00"))
    await service.update_reservation(slug, booked.id, GUEST, "k1", starts_at=at("20:00"))
    newer = await service.update_reservation(slug, booked.id, GUEST, "k2", starts_at=at("21:00"))

    late = await service.update_reservation(slug, booked.id, GUEST, "k1", starts_at=at("20:00"))

    assert late == newer
    assert await stored(engine, slug, booked.id) == (at("21:00"), 2, "confirmed")


@pytest.mark.parametrize(
    "change", [{"starts_at": "21:00"}, {"party_size": 3}, {"starts_at": "20:00", "party_size": 3}]
)
async def test_an_update_key_reused_for_another_change_is_invalid_input(
    engine: AsyncEngine,
    service: PgAgentService,
    make_merchant: MerchantFactory,
    change: dict[str, object],
) -> None:
    slug = await make_merchant()
    booked = await book(service, slug, at("19:00"))
    await service.update_reservation(slug, booked.id, GUEST, "k1", starts_at=at("20:00"))
    starts_at = change.get("starts_at")
    party_size = change.get("party_size")

    with pytest.raises(InvalidInput, match="reused with different parameters"):
        await service.update_reservation(
            slug,
            booked.id,
            GUEST,
            "k1",
            starts_at=at(str(starts_at)) if starts_at else None,
            party_size=int(str(party_size)) if party_size else None,
        )

    assert await stored(engine, slug, booked.id) == (at("20:00"), 2, "confirmed")


async def test_an_update_key_reused_on_another_booking_is_invalid_input(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    first = await book(service, slug, at("19:00"))
    second = await book(service, slug, at("19:30"))
    await service.update_reservation(slug, first.id, GUEST, "k1", starts_at=at("20:00"))

    with pytest.raises(InvalidInput, match="reused with different parameters"):
        await service.update_reservation(slug, second.id, GUEST, "k1", starts_at=at("20:00"))


async def test_an_update_that_failed_does_not_use_up_its_key(
    engine: AsyncEngine, service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(tables=ONE_TABLE, services=[{"start": "19:00", "end": "23:00"}])
    await book(service, slug, at("19:00"))
    later = await book(service, slug, at("21:00"))
    with pytest.raises(SlotUnavailable):
        await service.update_reservation(slug, later.id, GUEST, "k1", starts_at=at("20:00"))

    moved = await service.update_reservation(slug, later.id, GUEST, "k1", starts_at=at("22:00"))

    assert moved.starts_at == at("22:00")


async def test_an_update_refused_after_its_claim_does_not_use_up_its_key(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    # The key is claimed before the status check: the refusal must roll it back.
    slug = await make_merchant()
    cancelled = await book(service, slug, at("19:00"))
    await service.cancel_reservation(slug, cancelled.id, by="guest", guest_name=GUEST)
    with pytest.raises(NotFound):
        await service.update_reservation(slug, cancelled.id, GUEST, "k1", starts_at=at("20:00"))
    other = await book(service, slug, at("19:30"))

    moved = await service.update_reservation(slug, other.id, GUEST, "k1", starts_at=at("20:00"))

    assert moved.starts_at == at("20:00")


async def test_two_concurrent_updates_with_one_key_apply_one_change(
    engine: AsyncEngine, service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    booked = await book(service, slug, at("19:00"))

    results = await asyncio.gather(
        *(
            service.update_reservation(slug, booked.id, GUEST, "k1", starts_at=at("20:00"))
            for _ in range(2)
        )
    )

    assert results[0] == results[1]
    async with engine.connect() as conn:
        changes = await conn.scalar(
            select(func.count())
            .select_from(reservation_changes_t)
            .where(
                reservation_changes_t.c.merchant_slug == slug,
                reservation_changes_t.c.idempotency_key == "k1",
            )
        )
    assert changes == 1


@pytest.mark.parametrize("guest_name", ["Alex Martin", "Sam"])
async def test_update_under_another_name_is_not_found(
    service: PgAgentService, make_merchant: MerchantFactory, guest_name: str
) -> None:
    slug = await make_merchant()
    reservation = await book(service, slug, at("20:00"))

    with pytest.raises(NotFound):
        await service.update_reservation(
            slug, reservation.id, guest_name, "move-1", starts_at=at("21:00")
        )


async def test_update_of_a_cancelled_booking_is_not_found(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    reservation = await book(service, slug, at("20:00"))
    await service.cancel_reservation(slug, reservation.id, by="guest", guest_name=GUEST)

    with pytest.raises(NotFound):
        await service.update_reservation(slug, reservation.id, GUEST, "m", starts_at=at("21:00"))


async def test_update_rejects_a_blocked_new_start(
    engine: AsyncEngine, service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    reservation = await book(service, slug, at("20:00"))
    async with merchant_conn(engine, slug) as conn:
        await conn.execute(insert(blocked_slots_t).values(starts_at=at("21:00")))

    with pytest.raises(SlotUnavailable, match="blocked"):
        await service.update_reservation(slug, reservation.id, GUEST, "m", starts_at=at("21:00"))


async def test_update_rejects_a_party_above_the_limit(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(max_party_size=6)
    reservation = await book(service, slug, at("20:00"))

    with pytest.raises(PartyTooLarge):
        await service.update_reservation(slug, reservation.id, GUEST, "m", party_size=7)


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
