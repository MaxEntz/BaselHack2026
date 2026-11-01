"""A guest's updates on a real Postgres 16: moves, party changes and their idempotency
keys (#78, ADR-0013)."""

import asyncio

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.service import PgAgentService
from core.db import blocked_slots_t, merchant_conn, reservation_changes_t
from core.errors import InvalidInput, NotFound, PartyTooLarge, SlotUnavailable
from tests.integration.booking_helpers import (
    DINNER,
    GUEST,
    NOW,
    ONE_TABLE,
    TWO_TABLES,
    TZ,
    Clock,
    MerchantFactory,
    at,
    book,
    stored,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def clock() -> Clock:
    return Clock(NOW)


@pytest.fixture
def service(engine: AsyncEngine, clock: Clock) -> PgAgentService:
    return PgAgentService(engine, tz=TZ, clock=clock)


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
