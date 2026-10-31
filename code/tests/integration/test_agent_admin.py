"""Merchant operations (#9) on a real Postgres 16, through `PgAgentService`."""

from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from agent.service import PgAgentService
from core.db import guest_notices_t, merchant_conn, merchants_t, policy_t, tables_t
from core.db import reservations_t as reservations
from core.errors import InvalidInput, NotFound, PartyTooLarge
from core.models import AdminReservationRow, Reservation, Service, TableSpec

pytestmark = pytest.mark.integration

MerchantFactory = Callable[..., Awaitable[str]]  # the `make_merchant` fixture

TZ = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 10, 31, 9, tzinfo=TZ)
DAY = date(2026, 11, 2)
GUEST = "Ada Lovelace"


def at(hhmm: str, day: date = DAY) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=TZ)


@pytest.fixture
def service(engine: AsyncEngine) -> PgAgentService:
    return PgAgentService(engine, tz=TZ, clock=lambda: NOW)


async def book(
    service: PgAgentService, slug: str, hhmm: str, party_size: int = 2, guest: str = GUEST
) -> Reservation:
    return await service.create_reservation(
        slug, at(hhmm), party_size, guest, str(uuid4()), via="claude"
    )


async def insert_booking(
    engine: AsyncEngine, slug: str, start: datetime, label: str = "T1", **values: Any
) -> None:
    """A booking written directly, for times the booking rules would refuse (late night)."""
    async with merchant_conn(engine, slug) as conn:
        table_id = await conn.scalar(select(tables_t.c.id).where(tables_t.c.label == label))
        await conn.execute(
            insert(reservations).values(
                table_id=table_id,
                **{
                    "guest_name": GUEST,
                    "party_size": 2,
                    "during": Range(start, start + timedelta(hours=2)),
                    "via": "website",
                    **values,
                },
            )
        )


async def registry_profile(engine: AsyncEngine, slug: str) -> dict[str, Any]:
    async with engine.connect() as conn:
        profile = await conn.scalar(select(merchants_t.c.profile).where(merchants_t.c.slug == slug))
    assert isinstance(profile, dict)
    return profile


async def floor_plan(engine: AsyncEngine, slug: str) -> list[tuple[str, int]]:
    async with merchant_conn(engine, slug) as conn:
        rows = await conn.execute(
            select(tables_t.c.label, tables_t.c.seats).order_by(tables_t.c.id)
        )
        return [(row.label, row.seats) for row in rows]


# list_reservations


async def test_list_reservations_sorted_by_time_with_local_times(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    late = await book(service, slug, "20:30", party_size=4)
    early = await book(service, slug, "19:00")
    lunch = await book(service, slug, "12:30", party_size=6, guest="Grace Hopper")

    rows = await service.list_reservations(slug, DAY)

    assert rows == [
        AdminReservationRow(
            id=lunch.id,
            time="12:30",
            guest_name="Grace Hopper",
            party_size=6,
            table_label=lunch.table_label,
            via="claude",
            status="confirmed",
        ),
        AdminReservationRow(
            id=early.id,
            time="19:00",
            guest_name=GUEST,
            party_size=2,
            table_label=early.table_label,
            via="claude",
            status="confirmed",
        ),
        AdminReservationRow(
            id=late.id,
            time="20:30",
            guest_name=GUEST,
            party_size=4,
            table_label=late.table_label,
            via="claude",
            status="confirmed",
        ),
    ]


async def test_list_reservations_uses_the_merchant_day_not_utc(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    # 23:30 local is 22:30 UTC on DAY; 00:30 local the next day is 23:30 UTC on DAY.
    await insert_booking(engine, slug, at("23:30"), id="R-900")
    await insert_booking(engine, slug, at("00:30", DAY + timedelta(days=1)), "T2", id="R-901")

    today = await service.list_reservations(slug, DAY)
    tomorrow = await service.list_reservations(slug, DAY + timedelta(days=1))

    assert [(row.id, row.time) for row in today] == [("R-900", "23:30")]
    assert [(row.id, row.time) for row in tomorrow] == [("R-901", "00:30")]


async def test_list_reservations_hides_cancelled_unless_asked(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    kept = await book(service, slug, "19:00")
    dropped = await book(service, slug, "20:00")
    await service.cancel_reservation(slug, dropped.id, by="guest", guest_name=GUEST)

    default = await service.list_reservations(slug, DAY)
    everything = await service.list_reservations(slug, DAY, include_cancelled=True)

    assert [row.id for row in default] == [kept.id]
    assert [(row.id, row.status) for row in everything] == [
        (kept.id, "confirmed"),
        (dropped.id, "cancelled"),
    ]


# get_stats


async def test_get_stats_on_a_known_evening(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    await book(service, slug, "19:00", party_size=2)
    await book(service, slug, "20:00", party_size=4)
    cancelled = await book(service, slug, "20:30", party_size=6)
    await service.cancel_reservation(slug, cancelled.id, by="guest", guest_name=GUEST)
    await service.block_slot(slug, at("21:00"))
    await service.block_slot(slug, at("12:00"))

    stats = await service.get_stats(slug, DAY)

    assert (stats.bookings, stats.guests, stats.seats) == (2, 6, 44)
    assert stats.blocked == ("12:00", "21:00")


async def test_get_stats_counts_only_that_day(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    await service.create_reservation(
        slug, at("19:00", DAY + timedelta(days=1)), 2, GUEST, str(uuid4()), via="claude"
    )
    await service.block_slot(slug, at("19:00", DAY + timedelta(days=1)))

    stats = await service.get_stats(slug, DAY)

    assert (stats.bookings, stats.guests, stats.blocked) == (0, 0, ())


# block_slot / unblock_slot


async def test_blocking_a_slot_hides_it_but_keeps_the_booking(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    existing = await book(service, slug, "21:00")

    async def offered() -> set[str]:
        slots = await service.get_availability(slug, DAY, 2, time="21:00")
        return {f"{slot.starts_at.astimezone(TZ):%H:%M}" for slot in slots}

    assert "21:00" in await offered()
    await service.block_slot(slug, at("21:00"))
    await service.block_slot(slug, at("21:00"))  # idempotent

    assert "21:00" not in await offered()
    assert [row.id for row in await service.list_reservations(slug, DAY)] == [existing.id]

    await service.unblock_slot(slug, at("21:00"))
    await service.unblock_slot(slug, at("21:00"))  # idempotent

    assert "21:00" in await offered()


async def test_blocking_a_time_that_is_not_a_start_lists_the_valid_ones(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()

    with pytest.raises(InvalidInput, match=r"20:15.*19:00, 19:30, 20:00, 20:30, 21:00"):
        await service.block_slot(slug, at("20:15"))


# update_policy


async def test_lowering_max_party_size_refuses_larger_groups(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    existing = await book(service, slug, "19:00", party_size=6)

    policy = await service.update_policy(slug, max_party_size=4)

    assert policy.max_party_size == 4
    assert policy.slot_length_min == 120
    with pytest.raises(PartyTooLarge):
        await service.get_availability(slug, DAY, 6)
    # Existing larger bookings are kept.
    assert [row.id for row in await service.list_reservations(slug, DAY)] == [existing.id]
    assert (await registry_profile(engine, slug))["max_party_size"] == 4


async def test_new_services_change_the_start_times(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()

    policy = await service.update_policy(slug, services=[Service(start="18:00", end="20:00")])

    assert policy.services == (Service(start="18:00", end="20:00"),)
    slots = await service.get_availability(slug, DAY, 2, time="18:00")
    assert {f"{s.starts_at.astimezone(TZ):%H:%M}" for s in slots} <= {"18:00", "18:30", "19:00"}
    assert (await registry_profile(engine, slug))["services"] == [
        {"start": "18:00", "end": "20:00"}
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"max_party_size": 0},
        {"services": []},
        {
            "services": [
                Service(start="18:00", end="20:00"),
                Service(start="19:00", end="21:00"),
            ]
        },
    ],
    ids=["nothing", "zero-guests", "no-service", "overlap"],
)
async def test_invalid_policy_changes_are_refused_and_change_nothing(
    service: PgAgentService,
    engine: AsyncEngine,
    make_merchant: MerchantFactory,
    changes: dict[str, Any],
) -> None:
    slug = await make_merchant()

    with pytest.raises(InvalidInput):
        await service.update_policy(slug, **changes)

    async with merchant_conn(engine, slug) as conn:
        policy = (await conn.execute(select(policy_t))).one()
    assert policy.max_party_size == 6
    assert len(policy.services) == 2


# update_tables


async def test_update_tables_replaces_the_floor_plan(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(
        tables=[{"label": "T1", "seats": 2}, {"label": "T2", "seats": 4}], seats=6
    )
    plan = [TableSpec(label="T1", seats=4), TableSpec(label="T3", seats=6)]

    result = await service.update_tables(slug, plan)

    assert result == plan
    assert await floor_plan(engine, slug) == [("T1", 4), ("T3", 6)]
    profile = await registry_profile(engine, slug)
    assert profile["tables"] == [{"label": "T1", "seats": 4}, {"label": "T3", "seats": 6}]
    assert profile["seats"] == 10


async def test_removing_a_booked_table_is_refused_and_changes_nothing(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(
        tables=[{"label": "T1", "seats": 2}, {"label": "T2", "seats": 4}], seats=6
    )
    booked = await book(service, slug, "19:00", party_size=4)
    assert booked.table_label == "T2"

    with pytest.raises(InvalidInput, match=rf"T2.*{booked.id}"):
        await service.update_tables(slug, [TableSpec(label="T1", seats=2)])

    assert await floor_plan(engine, slug) == [("T1", 2), ("T2", 4)]


async def test_shrinking_a_booked_table_below_its_party_is_refused(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(tables=[{"label": "T1", "seats": 4}], seats=4)
    booked = await book(service, slug, "19:00", party_size=4)

    with pytest.raises(InvalidInput, match=booked.id):
        await service.update_tables(slug, [TableSpec(label="T1", seats=2)])


async def test_a_table_with_past_bookings_cannot_be_removed(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(
        tables=[{"label": "T1", "seats": 2}, {"label": "T2", "seats": 4}], seats=6
    )
    await insert_booking(engine, slug, NOW - timedelta(days=3), id="R-800")

    with pytest.raises(InvalidInput, match=r"T1.*past bookings"):
        await service.update_tables(slug, [TableSpec(label="T2", seats=4)])


@pytest.mark.parametrize(
    "plan",
    [[], [TableSpec(label="T1", seats=2), TableSpec(label="T1", seats=4)]],
    ids=["empty", "duplicate-label"],
)
async def test_invalid_floor_plans_are_refused(
    service: PgAgentService, make_merchant: MerchantFactory, plan: list[TableSpec]
) -> None:
    slug = await make_merchant()

    with pytest.raises(InvalidInput):
        await service.update_tables(slug, plan)


# cancel_reservation(by="merchant")


async def test_merchant_cancellation_leaves_one_notice_for_the_guest(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    booking = await book(service, slug, "20:00", party_size=4)

    cancelled = await service.cancel_reservation(slug, booking.id.lower(), by="merchant")
    again = await service.cancel_reservation(slug, booking.id, by="merchant")

    assert (cancelled.id, cancelled.status) == (booking.id, "cancelled")
    assert again.status == "cancelled"
    async with merchant_conn(engine, slug) as conn:
        cancelled_by = await conn.scalar(
            select(reservations.c.cancelled_by).where(reservations.c.id == booking.id)
        )
    assert cancelled_by == "merchant"

    upcoming, notices = await service.find_guest_reservations(GUEST)
    _, notices_again = await service.find_guest_reservations(GUEST)

    assert booking.id not in {r.id for r in upcoming}
    assert notices == [
        f"Le Petit Zinc cancelled your booking {booking.id} on 2026-11-02 at 20:00. "
        "Please contact them at http://localhost:8080/ or book again."
    ]
    assert notices_again == []
    async with engine.connect() as conn:
        count = await conn.scalar(
            select(func.count())
            .select_from(guest_notices_t)
            .where(guest_notices_t.c.merchant_slug == slug)
        )
    assert count == 1


async def test_merchant_cancellation_of_an_unknown_booking(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()

    with pytest.raises(NotFound):
        await service.cancel_reservation(slug, "R-999", by="merchant")


# Unknown merchant


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.list_reservations("no-such-merchant", DAY),
        lambda s: s.get_stats("no-such-merchant", DAY),
        lambda s: s.block_slot("no-such-merchant", at("19:00")),
        lambda s: s.unblock_slot("no-such-merchant", at("19:00")),
        lambda s: s.update_policy("no-such-merchant", max_party_size=4),
        lambda s: s.update_tables("no-such-merchant", [TableSpec(label="T1", seats=2)]),
        lambda s: s.cancel_reservation("no-such-merchant", "R-001", by="merchant"),
    ],
    ids=["list", "stats", "block", "unblock", "policy", "tables", "cancel"],
)
async def test_unknown_merchant_is_not_found(
    service: PgAgentService, call: Callable[[PgAgentService], Awaitable[object]]
) -> None:
    with pytest.raises(NotFound):
        await call(service)


# Races with bookings (the booking path reads the floor plan without locking it)


@pytest.mark.parametrize(
    "new_plan",
    [
        [TableSpec(label="T1", seats=2), TableSpec(label="T2", seats=4)],
        [TableSpec(label="T2", seats=4)],
    ],
    ids=["T1-shrunk", "T1-removed"],
)
async def test_a_booking_racing_a_floor_plan_change_takes_a_table_that_still_fits(
    service: PgAgentService,
    engine: AsyncEngine,
    make_merchant: MerchantFactory,
    monkeypatch: pytest.MonkeyPatch,
    new_plan: list[TableSpec],
) -> None:
    slug = await make_merchant(
        tables=[{"label": "T1", "seats": 4}, {"label": "T2", "seats": 4}], seats=8
    )
    other = PgAgentService(engine, tz=TZ, clock=lambda: NOW)
    original_load = PgAgentService._load
    raced: list[bool] = []

    async def load_then_change_the_plan(
        self: PgAgentService, conn: AsyncConnection, merchant_slug: str
    ) -> Any:
        merchant = await original_load(self, conn, merchant_slug)
        if not raced:  # once: the floor plan changes right after the booking read it
            raced.append(True)
            await other.update_tables(slug, new_plan)
        return merchant

    monkeypatch.setattr(PgAgentService, "_load", load_then_change_the_plan)

    booking = await book(service, slug, "19:00", party_size=4)

    assert raced == [True]
    assert booking.table_label == "T2"


# Input outside what the database can store


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda s, slug: s.update_policy(slug, max_party_size=3_000_000_000), "max_party_size"),
        (
            lambda s, slug: s.update_tables(slug, [TableSpec(label="T1", seats=3_000_000_000)]),
            "at most",
        ),
        (lambda s, slug: s.update_tables(slug, [TableSpec(label="T\x001", seats=2)]), "printable"),
        (lambda s, slug: s.list_reservations(slug, date.max), "out of range"),
    ],
    ids=["huge-party", "huge-table", "nul-label", "last-date"],
)
async def test_out_of_range_input_is_invalid_input_not_a_db_error(
    service: PgAgentService,
    make_merchant: MerchantFactory,
    call: Callable[[PgAgentService, str], Awaitable[object]],
    message: str,
) -> None:
    slug = await make_merchant()

    with pytest.raises(InvalidInput, match=message):
        await call(service, slug)


async def test_a_reservation_id_with_control_characters_is_not_found(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()

    with pytest.raises(NotFound):
        await service.cancel_reservation(slug, "R-\x00001", by="merchant")


async def test_cancelling_a_finished_booking_sends_no_notice(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    await insert_booking(engine, slug, NOW - timedelta(days=1), id="R-700", guest_name="Past Guest")

    cancelled = await service.cancel_reservation(slug, "R-700", by="merchant")
    _, notices = await service.find_guest_reservations("Past Guest")

    assert cancelled.status == "cancelled"
    assert notices == []
