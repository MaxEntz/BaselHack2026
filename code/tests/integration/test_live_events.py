"""LISTEN/NOTIFY → LiveBus on a real Postgres 16: what the merchant chat sees live.

Reservations are written straight to the merchant's table: the trigger fires the same
whoever writes them (the agent service, #8, isn't merged yet).
"""

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from sqlalchemy import event, insert, update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.schema import CreateSchema, DropSchema

from core.db import (
    merchant_conn,
    merchant_metadata,
    merchants_t,
    next_reservation_id,
    reservations_t,
    schema_for,
    tables_t,
)
from core.models import LiveEvent, Via
from merchant_platform.events import LiveBus, LiveListener

pytestmark = pytest.mark.integration

TZ = ZoneInfo("Europe/Zurich")
EIGHT_PM = datetime(2026, 10, 31, 20, tzinfo=TZ)
DEADLINE_S = 2  # AGENTS.md §9: a booking shows up in the merchant chat within 2 s.


async def add_merchant(engine: AsyncEngine, slug: str) -> None:
    """Registry row + schema with two tables, as provisioning (#7) would leave them."""
    async with engine.begin() as conn:
        await conn.execute(
            insert(merchants_t).values(
                slug=slug,
                name=slug,
                website="http://localhost:8080/",
                fallback_url="http://localhost:8080/reservation.html",
                price_level="€€",
                status="live",
                profile={},
            )
        )
    async with merchant_conn(engine, slug) as conn:
        await conn.execute(CreateSchema(schema_for(slug)))
        await conn.run_sync(merchant_metadata.create_all)
        await conn.execute(
            insert(tables_t), [{"label": "T1", "seats": 2}, {"label": "T5", "seats": 4}]
        )


async def drop_merchant(engine: AsyncEngine, slug: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(DropSchema(schema_for(slug), cascade=True))
        await conn.execute(merchants_t.delete().where(merchants_t.c.slug == slug))


async def book(
    engine: AsyncEngine, slug: str, *, via: Via = "claude", at: datetime = EIGHT_PM
) -> str:
    async with merchant_conn(engine, slug) as conn:
        reservation_id = await next_reservation_id(conn)
        await conn.execute(
            insert(reservations_t).values(
                id=reservation_id,
                table_id=2,
                guest_name="Sam Dupont",
                party_size=4,
                during=Range(at, at + timedelta(minutes=120)),
                via=via,
            )
        )
    return reservation_id


async def next_event(queue: asyncio.Queue[LiveEvent]) -> LiveEvent:
    return await asyncio.wait_for(queue.get(), DEADLINE_S)


@pytest.fixture
async def merchants(engine: AsyncEngine) -> AsyncIterator[tuple[str, str]]:
    slugs = (f"zinc-{uuid4().hex[:8]}", f"mimi-{uuid4().hex[:8]}")
    for slug in slugs:
        await add_merchant(engine, slug)
    yield slugs
    for slug in slugs:
        await drop_merchant(engine, slug)


@pytest.fixture
def bus() -> LiveBus:
    return LiveBus()


@pytest.fixture
async def listener(
    database_url: str, engine: AsyncEngine, bus: LiveBus
) -> AsyncIterator[LiveListener]:
    listener = LiveListener(database_url, engine, bus, "Europe/Zurich")
    task = asyncio.create_task(listener.run())
    await asyncio.wait_for(listener.listening.wait(), 5)
    yield listener
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_a_booking_reaches_the_merchants_subscribers_within_2_s(
    engine: AsyncEngine, merchants: tuple[str, str], listener: LiveListener, bus: LiveBus
) -> None:
    slug = merchants[0]
    queue = bus.subscribe(slug)

    reservation_id = await book(engine, slug, via="claude")

    event = await next_event(queue)
    assert event.kind == "new_booking"
    assert event.via == "claude"
    assert event.reservation.model_dump() == {
        "id": reservation_id,
        "time": "20:00",
        "guest_name": "Sam Dupont",
        "party_size": 4,
        "table_label": "T5",
        "via": "claude",
        "status": "confirmed",
    }


async def test_website_bookings_updates_and_cancellations(
    engine: AsyncEngine, merchants: tuple[str, str], listener: LiveListener, bus: LiveBus
) -> None:
    slug = merchants[0]
    queue = bus.subscribe(slug)
    reservation_id = await book(engine, slug, via="website")
    assert (await next_event(queue)).via == "website"

    async with merchant_conn(engine, slug) as conn:
        later = EIGHT_PM + timedelta(minutes=30)
        await conn.execute(
            update(reservations_t)
            .where(reservations_t.c.id == reservation_id)
            .values(during=Range(later, later + timedelta(minutes=120)))
        )
    updated = await next_event(queue)
    assert (updated.kind, updated.reservation.time) == ("updated", "20:30")

    async with merchant_conn(engine, slug) as conn:
        await conn.execute(
            update(reservations_t)
            .where(reservations_t.c.id == reservation_id)
            .values(status="cancelled", cancelled_by="merchant")
        )
    cancelled = await next_event(queue)
    assert (cancelled.kind, cancelled.reservation.status) == ("cancelled", "cancelled")


async def test_merchants_only_receive_their_own_events(
    engine: AsyncEngine, merchants: tuple[str, str], listener: LiveListener, bus: LiveBus
) -> None:
    zinc, mimi = merchants
    zinc_queue, mimi_queue = bus.subscribe(zinc), bus.subscribe(mimi)

    mimi_booking = await book(engine, mimi)
    zinc_booking = await book(engine, zinc)

    assert (await next_event(zinc_queue)).reservation.id == zinc_booking
    assert (await next_event(mimi_queue)).reservation.id == mimi_booking
    assert zinc_queue.empty()
    assert mimi_queue.empty()


@contextmanager
def statements(engine: AsyncEngine) -> Iterator[list[str]]:
    """The SQL statements `engine` runs inside the block."""
    seen: list[str] = []

    def record(*args: Any) -> None:
        seen.append(args[2])

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)


def payload(schema: str) -> str:
    return json.dumps(
        {"schema": schema, "op": "INSERT", "id": "R-001", "status": "confirmed", "via": "claude"}
    )


@pytest.mark.parametrize("schema", ["public", "pg_catalog", "m_x", "m_Zinc; DROP", "m_a-b"])
async def test_a_schema_that_is_not_a_merchants_runs_no_query(
    engine: AsyncEngine, schema: str
) -> None:
    bus = LiveBus()
    listener = LiveListener("postgresql+psycopg://unused/db", engine, bus, "Europe/Zurich")

    with statements(engine) as seen:
        await listener.handle(payload(schema))

    assert seen == []


async def test_an_unregistered_merchant_schema_is_ignored(engine: AsyncEngine) -> None:
    bus = LiveBus()
    queue = bus.subscribe("ghost-merchant")
    listener = LiveListener("postgresql+psycopg://unused/db", engine, bus, "Europe/Zurich")

    with statements(engine) as seen:
        await listener.handle(payload("m_ghost_merchant"))

    # Only the registry lookup: nothing is read from the unknown schema.
    assert len(seen) == 1
    assert "public.merchants" in seen[0]
    assert "reservations" not in seen[0]
    assert queue.empty()


@pytest.mark.parametrize("bad", ["not json", "{}", '{"schema": "m_x", "op": "DELETE"}'])
async def test_malformed_notifications_are_skipped(engine: AsyncEngine, bad: str) -> None:
    listener = LiveListener("postgresql+psycopg://unused/db", engine, LiveBus(), "Europe/Zurich")
    await listener.handle(bad)


async def test_the_listener_reconnects_after_its_connection_is_killed(
    libpq_database_url: str,
    engine: AsyncEngine,
    merchants: tuple[str, str],
    listener: LiveListener,
    bus: LiveBus,
) -> None:
    slug = merchants[0]
    queue = bus.subscribe(slug)
    async with await psycopg.AsyncConnection.connect(libpq_database_url, autocommit=True) as admin:
        cursor = await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE query = %s",
            ("LISTEN reservations",),
        )
        assert [row[0] for row in await cursor.fetchall()] == [True]

    # First retry after 1 s.
    async with asyncio.timeout(5):
        while listener.listening.is_set():  # noqa: ASYNC110
            await asyncio.sleep(0.01)
        await listener.listening.wait()

    reservation_id = await book(engine, slug)
    assert (await next_event(queue)).reservation.id == reservation_id


async def test_a_rolled_back_booking_sends_nothing(
    engine: AsyncEngine, merchants: tuple[str, str], listener: LiveListener, bus: LiveBus
) -> None:
    slug = merchants[0]
    queue = bus.subscribe(slug)
    async with engine.connect() as conn:
        translated = await conn.execution_options(
            schema_translate_map={"merchant": schema_for(slug)}
        )
        transaction = await translated.begin()
        await translated.execute(
            insert(reservations_t).values(
                id="R-999",
                table_id=1,
                guest_name="Nobody",
                party_size=2,
                during=Range(EIGHT_PM, EIGHT_PM + timedelta(minutes=120)),
                via="claude",
            )
        )
        await transaction.rollback()

    committed = await book(engine, slug, at=EIGHT_PM + timedelta(hours=1))

    # Notifications arrive in commit order: the first one is the committed booking.
    assert (await next_event(queue)).reservation.id == committed
    assert queue.empty()
