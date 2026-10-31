"""The management site's bookings (#134) on a real Postgres 16, through `ManagementService`."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.management import ManagementService
from agent.service import PgAgentService
from core.config import Settings
from core.db import guest_notices_t, reservation_changes_t
from core.errors import InvalidInput, NotFound, SlotUnavailable
from core.models import (
    DaySlots,
    ManagedReservation,
    MerchantBookingInput,
    MoveBookingInput,
    ReservationDay,
)
from merchant_platform.api import create_app

pytestmark = pytest.mark.integration

MerchantFactory = Callable[..., Awaitable[str]]  # the `make_merchant` fixture

TZ = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 10, 31, 9, tzinfo=TZ)
DAY = date(
    2026, 11, 2
)  # Le Petit Zinc: 12:00-14:00, 19:00-22:00; T1-T4 2 seats, T5-T10 4, T11-T12 6


def at(hhmm: str, day: date = DAY) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=TZ)


def services(engine: AsyncEngine, now: datetime = NOW) -> ManagementService:
    agent = PgAgentService(engine, tz=TZ, clock=lambda: now)
    return ManagementService(engine, agent, tz=TZ, clock=lambda: now)


async def add(
    manage: ManagementService, slug: str, hhmm: str, party_size: int = 2, guest: str = "Ada"
) -> ManagedReservation:
    body = MerchantBookingInput(
        starts_at=at(hhmm), party_size=party_size, guest_name=guest, idempotency_key=str(uuid4())
    )
    return await manage.add(slug, body)


async def guest_booking(engine: AsyncEngine, slug: str, hhmm: str, party_size: int = 2) -> str:
    agent = PgAgentService(engine, tz=TZ, clock=lambda: NOW)
    booked = await agent.create_reservation(
        slug, at(hhmm), party_size, "Sam Dupont", str(uuid4()), via="claude"
    )
    return booked.id


def move(hhmm: str, key: str | None = None, day: date = DAY) -> MoveBookingInput:
    return MoveBookingInput(starts_at=at(hhmm, day), idempotency_key=key or str(uuid4()))


async def notices(engine: AsyncEngine, reservation_id: str, slug: str) -> int:
    async with engine.connect() as conn:
        count = await conn.scalar(
            select(func.count())
            .select_from(guest_notices_t)
            .where(
                guest_notices_t.c.merchant_slug == slug,
                guest_notices_t.c.reservation_id == reservation_id,
            )
        )
    return count or 0


async def test_a_day_holds_its_grid_blocked_times_and_every_booking(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    manage = services(engine)
    kept = await add(manage, slug, "19:00", guest="Kim")
    dropped = await add(manage, slug, "12:30", guest="Lou")
    await manage.cancel(slug, dropped.id)
    await manage.agent.block_slot(slug, at("20:30"))

    day = await manage.day(slug, DAY)

    assert [(s.start, s.end) for s in day.services] == [("12:00", "14:00"), ("19:00", "22:00")]
    assert day.starts == ("12:00", "12:30", "13:00", "19:00", "19:30", "20:00", "20:30", "21:00")
    assert (day.slot_length_min, day.largest_group, day.blocked) == (120, 6, ("20:30",))
    assert [(r.id, r.time, r.status, r.cancelled_by, r.via) for r in day.reservations] == [
        (dropped.id, "12:30", "cancelled", "merchant", "merchant"),
        (kept.id, "19:00", "confirmed", None, "merchant"),
    ]
    assert kept.date == DAY
    assert kept.created_at.tzinfo is not None


async def test_slots_list_every_start_left_with_its_table(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    manage = services(engine, now=at("19:40"))
    # Every 6-seat table taken at 20:00 except by the booking we may leave out.
    first = await guest_booking(engine, slug, "12:00", party_size=6)  # T11 at lunch
    del first
    evening = services(engine)
    a = await add(evening, slug, "19:30", party_size=6, guest="A")
    b = await add(evening, slug, "20:00", party_size=6, guest="B")
    await evening.agent.block_slot(slug, at("21:00"))

    six = await manage.slots(slug, DAY, 6)
    leaving_b = await manage.slots(slug, DAY, 6, ignore=b.id.lower())
    too_many = await manage.slots(slug, DAY, 7)

    assert [s.time for s in six.slots] == ["20:00", "20:30", "21:00"]  # 19:40: lunch is over
    assert [(s.table.label if s.table else None) for s in six.slots] == [None, None, None]
    assert [s.blocked for s in six.slots] == [False, False, True]
    assert six.slots[0].until == "22:00"
    assert leaving_b.slots[0].table is not None
    assert leaving_b.slots[0].table.label == {"T11": "T12", "T12": "T11"}[a.table_label]
    assert all(s.table is None for s in too_many.slots)


async def test_adding_books_for_the_merchant_and_a_replay_changes_nothing(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    manage = services(engine)
    body = MerchantBookingInput(
        starts_at=at("20:00"), party_size=3, guest_name="Kim Weber", idempotency_key="web-1"
    )

    added = await manage.add(slug, body)
    again = await manage.add(slug, body)

    assert again == added
    # Smallest fit, then label as a string: "T10" < "T5" (core/availability.py `pick_table`).
    assert (added.via, added.table_label, added.party_size) == ("merchant", "T10", 3)
    with pytest.raises(SlotUnavailable):
        past = body.model_copy(update={"starts_at": at("20:00", date(2026, 10, 30))})
        await manage.add(slug, past.model_copy(update={"idempotency_key": "web-2"}))


async def test_a_move_takes_the_smallest_free_table_of_the_new_time(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    manage = services(engine)
    booking = await add(manage, slug, "19:00", party_size=2)
    for _ in range(4):  # every 2-seat table at 21:00
        await add(manage, slug, "21:00", party_size=2, guest="Busy")

    moved = await manage.move(slug, booking.id.lower(), move("21:00", "m-1"))

    assert (moved.id, moved.time, moved.via, moved.party_size) == (
        booking.id,
        "21:00",
        "merchant",
        2,
    )
    assert moved.table_label == "T10"  # the smallest one left, "T10" < "T5"
    still = await manage.move(slug, booking.id, move("20:30"))
    assert still.table_label == "T10"  # 2-seat tables still taken: it keeps its own
    back = await manage.move(slug, booking.id, move("19:00"))
    assert back.table_label == "T1"  # free again before 21:00: the smallest fit


async def test_a_move_is_idempotent_and_keeps_its_key(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    manage = services(engine)
    booking = await add(manage, slug, "19:00")
    await manage.move(slug, booking.id, move("20:00", "m-1"))
    await manage.move(slug, booking.id, move("21:00", "m-2"))

    replayed = await manage.move(slug, booking.id, move("20:00", "m-1"))

    assert replayed.time == "21:00"  # the late retry doesn't undo the newer move (ADR-0013)
    with pytest.raises(InvalidInput):
        await manage.move(slug, booking.id, move("19:30", "m-1"))
    async with engine.connect() as conn:
        keys = set(
            await conn.scalars(
                select(reservation_changes_t.c.idempotency_key).where(
                    reservation_changes_t.c.merchant_slug == slug
                )
            )
        )
    assert keys == {"m-1", "m-2"}


async def test_a_failed_move_leaves_the_booking_and_frees_its_key(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    manage = services(engine)
    booking = await add(manage, slug, "19:00")
    await manage.agent.block_slot(slug, at("20:00"))

    with pytest.raises(SlotUnavailable):
        await manage.move(slug, booking.id, move("20:00", "m-1"))
    with pytest.raises(InvalidInput):
        await manage.move(slug, booking.id, move("20:00", "m-2", day=date(2026, 11, 3)))
    with pytest.raises(SlotUnavailable):
        await manage.move(slug, booking.id, move("19:15", "m-3"))  # not a start time

    assert (await manage.move(slug, booking.id, move("20:30", "m-1"))).time == "20:30"


async def test_a_started_or_cancelled_booking_doesnt_move(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    booking = await add(services(engine), slug, "19:00")
    cancelled = await add(services(engine), slug, "19:00", guest="Lou")
    await services(engine).cancel(slug, cancelled.id)
    during = services(engine, now=at("19:10"))

    with pytest.raises(SlotUnavailable, match="already started"):
        await during.move(slug, booking.id, move("20:30"))
    with pytest.raises(SlotUnavailable, match="cancelled"):
        await services(engine).move(slug, cancelled.id, move("20:30"))
    with pytest.raises(NotFound):
        await services(engine).move(slug, "R-999", move("20:30"))


async def test_two_moves_to_the_last_table_leave_one_winner(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    manage = services(engine)
    a = await add(manage, slug, "19:00", party_size=6, guest="A")
    b = await add(manage, slug, "19:00", party_size=6, guest="B")
    await add(manage, slug, "21:00", party_size=6, guest="C")  # one 6-seat table left at 21:00

    outcomes = await asyncio.gather(
        manage.move(slug, a.id, move("21:00")),
        manage.move(slug, b.id, move("21:00")),
        return_exceptions=True,
    )

    moved = [o for o in outcomes if isinstance(o, ManagedReservation)]
    refused = [o for o in outcomes if isinstance(o, SlotUnavailable)]
    assert (len(moved), len(refused)) == (1, 1)
    day = await manage.day(slug, DAY)
    at_nine = [r for r in day.reservations if r.time == "21:00" and r.status == "confirmed"]
    assert sorted(r.table_label for r in at_nine) == ["T11", "T12"]


async def test_a_guests_booking_gets_a_notice_and_the_merchants_own_doesnt(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    manage = services(engine)
    guests = await guest_booking(engine, slug, "19:00")
    own = await add(manage, slug, "19:00")

    told = await manage.cancel(slug, guests)
    untold = await manage.cancel(slug, own.id)

    assert (told.outcome, told.reservation.cancelled_by) == ("notified", "merchant")
    assert (untold.outcome, untold.reservation.status) == ("own_booking", "cancelled")
    assert (await notices(engine, guests, slug), await notices(engine, own.id, slug)) == (1, 0)
    assert (await manage.cancel(slug, own.id)).outcome == "already_cancelled"


async def test_the_routes_add_move_cancel_and_list_for_the_owner(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url="postgresql+psycopg://u:p@localhost:5432/db",
        platform_merchant=slug,  # development owner: the cookie is #132's to test
    )
    app = create_app(settings, bookings=services(engine))
    base = f"/merchants/{slug}"
    booking = {"starts_at": at("20:00").isoformat(), "party_size": 2, "guest_name": "Kim"}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        added = await http.post(f"{base}/reservations", json=booking | {"idempotency_key": "k1"})
        rid = added.json()["id"]
        moved = await http.patch(
            f"{base}/reservations/{rid}",
            json={"starts_at": at("21:00").isoformat(), "idempotency_key": "k2"},
        )
        taken = await http.patch(
            f"{base}/reservations/{rid}",
            json={"starts_at": at("21:15").isoformat(), "idempotency_key": "k3"},
        )
        slots = await http.get(f"{base}/slots", params={"date": str(DAY), "party_size": 2})
        cancelled = await http.post(f"{base}/reservations/{rid}/cancel")
        day = await http.get(f"{base}/reservations", params={"date": str(DAY)})
        unknown = await http.post(f"{base}/reservations/R-999/cancel")

    assert added.status_code == 201
    assert ManagedReservation.model_validate(added.json()).via == "merchant"
    assert (moved.status_code, moved.json()["time"]) == (200, "21:00")
    assert (taken.status_code, taken.json()["error"]) == (409, "slot_unavailable")
    assert DaySlots.model_validate(slots.json()).slots[0].time == "12:00"
    assert cancelled.json()["outcome"] == "own_booking"
    listed = ReservationDay.model_validate(day.json())
    assert [(r.id, r.status) for r in listed.reservations] == [(rid, "cancelled")]
    assert (unknown.status_code, unknown.json()["error"]) == (404, "not_found")


async def test_slots_for_a_move_match_what_the_move_does(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    manage = services(engine)
    six = await add(manage, slug, "19:00", party_size=6)
    await manage.agent.update_policy(slug, max_party_size=4)  # below the booking's party
    first = await add(manage, slug, "19:00", party_size=2, guest="Ada")  # T1
    pair = await add(manage, slug, "19:00", party_size=2, guest="Bea")  # T2
    await manage.cancel(slug, first.id)  # T1 free again: smaller label, same seats

    for_six = await manage.slots(slug, DAY, 6, ignore=six.id)
    for_pair = await manage.slots(slug, DAY, 2, ignore=pair.id)
    moved = await manage.move(slug, pair.id, move("21:00"))

    assert any(s.table for s in for_six.slots)  # the move would take it, so the dialog offers it
    nine = next(s for s in for_pair.slots if s.time == "21:00")
    assert moved.table_label == "T2"  # it keeps its own table among equals
    assert nine.table is not None and nine.table.label == "T2"  # and the dialog says so


async def test_a_slot_ends_after_its_real_minutes_on_a_clock_change_night(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    night = [{"start": "00:00", "end": "04:00"}]
    slug = await make_merchant(services=night)
    change = date(2027, 3, 28)  # 02:00 jumps to 03:00

    slots = await services(engine, now=at("09:00", date(2027, 3, 27))).slots(slug, change, 2)

    assert {s.time: s.until for s in slots.slots}["01:00"] == "04:00"


async def test_moving_a_guests_booking_tells_the_guest(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    manage = services(engine)
    guests = await guest_booking(engine, slug, "19:00")
    own = await add(manage, slug, "19:00")

    await manage.move(slug, guests, move("20:30"))
    await manage.move(slug, own.id, move("20:30"))

    assert (await notices(engine, guests, slug), await notices(engine, own.id, slug)) == (1, 0)
    _, told = await manage.agent.find_guest_reservations("Sam Dupont")
    assert any("moved your booking" in notice and "20:30" in notice for notice in told)


async def test_two_moves_with_one_key_apply_once(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    manage = services(engine)
    booking = await add(manage, slug, "19:00")

    first, second = await asyncio.gather(
        manage.move(slug, booking.id, move("20:30", "same")),
        manage.move(slug, booking.id, move("20:30", "same")),
    )

    assert first == second
    assert first.time == "20:30"


async def test_a_guest_update_and_a_merchant_move_of_one_booking_dont_deadlock(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    manage = services(engine)
    booking = await guest_booking(engine, slug, "19:00")

    outcomes = await asyncio.gather(
        manage.move(slug, booking, move("20:30")),
        manage.agent.update_reservation(
            slug, booking, "Sam Dupont", str(uuid4()), starts_at=at("21:00")
        ),
        return_exceptions=True,
    )

    assert not [o for o in outcomes if isinstance(o, BaseException)]
    final = await manage.reservation(slug, booking)
    assert final.time in ("20:30", "21:00")  # the one that took the lock second wins
