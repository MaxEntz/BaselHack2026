"""Public schema migration and schema-per-merchant isolation on a real Postgres 16."""

import json
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Table, create_engine, delete, func, insert, inspect, select, text, update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.schema import CreateSchema, DropSchema

from core.db import (
    MERCHANT,
    agent_requests_t,
    as_merchant,
    guest_notices_t,
    merchant_activity_t,
    merchant_conn,
    merchant_metadata,
    merchant_owners_t,
    merchants_t,
    next_reservation_id,
    reservation_changes_t,
    reservations_t,
    schema_for,
    tables_t,
)

pytestmark = pytest.mark.integration

PUBLIC_TABLES = {
    "merchants",
    "guest_notices",
    "reservation_changes",
    "agent_requests",
    "merchant_owners",
    "merchant_activity",
}
MERCHANT_TABLES = {"tables", "policy", "blocked_slots", "reservations"}
MIGRATED = {
    "tables": PUBLIC_TABLES,
    "functions": {"notify_reservation"},
    "extensions": {"btree_gist"},
}
EIGHT_PM = datetime(2026, 10, 31, 20, tzinfo=ZoneInfo("Europe/Zurich"))


def sqlstate(error: DBAPIError) -> str | None:
    state: str | None = getattr(error.orig, "sqlstate", None)
    return state


def public_objects(url: str) -> dict[str, set[str]]:
    """What migration 0001 owns in the database: public tables, function, extension."""
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            tables = set(inspect(conn).get_table_names(schema="public")) - {"alembic_version"}
            functions = set(
                conn.scalars(
                    text(
                        "SELECT proname FROM pg_proc "
                        "WHERE pronamespace = 'public'::regnamespace AND proname = :name"
                    ),
                    {"name": "notify_reservation"},
                )
            )
            extensions = set(conn.scalars(text("SELECT extname FROM pg_extension")))
    finally:
        engine.dispose()
    return {
        "tables": tables,
        "functions": functions,
        "extensions": extensions & {"btree_gist"},
    }


def test_migration_upgrades_downgrades_and_upgrades_again(
    empty_database_url: str, alembic_config_for: Callable[[str], Config]
) -> None:
    config = alembic_config_for(empty_database_url)

    command.upgrade(config, "head")
    assert public_objects(empty_database_url) == MIGRATED

    command.downgrade(config, "base")
    assert public_objects(empty_database_url) == {
        "tables": set(),
        "functions": set(),
        "extensions": set(),
    }

    command.upgrade(config, "head")
    assert public_objects(empty_database_url) == MIGRATED


def test_downgrade_refuses_while_a_merchant_schema_exists(
    empty_database_url: str, alembic_config_for: Callable[[str], Config]
) -> None:
    config = alembic_config_for(empty_database_url)
    command.upgrade(config, "head")
    schema = schema_for("le-petit-zinc")
    engine = create_engine(empty_database_url)
    try:
        with engine.begin() as conn:
            conn.execute(CreateSchema(schema))
            merchant_metadata.create_all(
                conn.execution_options(schema_translate_map={MERCHANT: schema})
            )
    finally:
        engine.dispose()

    with pytest.raises(DBAPIError) as raised:
        command.downgrade(config, "base")

    assert sqlstate(raised.value) == "2BP01"  # dependent_objects_still_exist
    assert public_objects(empty_database_url) == MIGRATED  # transactional DDL: nothing dropped


@pytest.mark.parametrize(
    "table",
    [
        merchants_t,
        guest_notices_t,
        reservation_changes_t,
        agent_requests_t,
        merchant_owners_t,
        merchant_activity_t,
    ],
    ids=lambda t: t.name,
)
async def test_public_tables_match_their_core_declarations(
    engine: AsyncEngine, table: Table
) -> None:
    async with engine.connect() as conn:
        reflected = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_columns(table.name, schema="public")
        )
    dialect = engine.dialect

    assert {
        column["name"]: (column["type"].compile(dialect=dialect), column["nullable"])
        for column in reflected
    } == {
        column.name: (column.type.compile(dialect=dialect), column.nullable)
        for column in table.columns
    }


def merchant_row(slug: str, **overrides: Any) -> dict[str, Any]:
    return {
        "slug": slug,
        "name": "Le Petit Zinc",
        "website": "http://localhost:8080/",
        "fallback_url": "http://localhost:8080/reservation.html",
        "price_level": "€€",
        "profile": {},
        **overrides,
    }


@pytest.mark.parametrize(
    "override",
    [{"slug": "Le_Petit_Zinc"}, {"price_level": "$"}, {"status": "closed"}],
    ids=["slug", "price_level", "status"],
)
async def test_registry_rejects_values_outside_the_contract(
    engine: AsyncEngine, override: dict[str, str]
) -> None:
    row = {**merchant_row(f"zinc-{uuid4().hex[:8]}"), **override}
    with pytest.raises(IntegrityError) as raised:
        async with engine.begin() as conn:
            await conn.execute(insert(merchants_t).values(row))

    assert sqlstate(raised.value) == "23514"  # check_violation


async def test_registry_starts_in_building_and_cascades_to_notices_keys_and_requests(
    engine: AsyncEngine,
) -> None:
    slug = f"zinc-{uuid4().hex[:8]}"
    async with engine.begin() as conn:
        status = await conn.scalar(
            insert(merchants_t).values(merchant_row(slug)).returning(merchants_t.c.status)
        )
        await conn.execute(
            insert(guest_notices_t).values(
                guest_name="Ada", merchant_slug=slug, reservation_id="R-001", message="Cancelled"
            )
        )
        await conn.execute(
            insert(reservation_changes_t).values(
                merchant_slug=slug, idempotency_key="k1", reservation_id="R-001", party_size=2
            )
        )
        await conn.execute(
            insert(agent_requests_t).values(
                merchant_slug=slug,
                agent_name="Claude",
                intent="PING",
                status=200,
                request={},
                response={},
            )
        )
    async with engine.begin() as conn:
        await conn.execute(delete(merchants_t).where(merchants_t.c.slug == slug))
        notices = await conn.scalar(
            select(func.count())
            .select_from(guest_notices_t)
            .where(guest_notices_t.c.merchant_slug == slug)
        )
        keys = await conn.scalar(
            select(func.count())
            .select_from(reservation_changes_t)
            .where(reservation_changes_t.c.merchant_slug == slug)
        )
        requests = await conn.scalar(
            select(func.count())
            .select_from(agent_requests_t)
            .where(agent_requests_t.c.merchant_slug == slug)
        )

    assert status == "building"
    assert (notices, keys, requests) == (0, 0, 0)  # deprovision deletes the registry row (ADR-0013)


@pytest.fixture
async def two_merchants(engine: AsyncEngine) -> AsyncIterator[tuple[str, str]]:
    """Two merchant schemas built from the Core declarations, dropped afterwards."""
    slugs = (f"zinc-{uuid4().hex[:8]}", f"mimi-{uuid4().hex[:8]}")
    for slug in slugs:
        async with merchant_conn(engine, slug) as conn:
            await conn.execute(CreateSchema(schema_for(slug)))
            await conn.run_sync(merchant_metadata.create_all)
    yield slugs
    async with engine.begin() as conn:
        for slug in slugs:
            await conn.execute(DropSchema(schema_for(slug), cascade=True))


async def test_merchant_objects_land_in_the_merchant_schema(
    engine: AsyncEngine, two_merchants: tuple[str, str]
) -> None:
    schema = schema_for(two_merchants[0])
    async with engine.connect() as conn:
        tables = await conn.run_sync(
            lambda sync_conn: set(inspect(sync_conn).get_table_names(schema=schema))
        )
        sequences = await conn.run_sync(
            lambda sync_conn: set(inspect(sync_conn).get_sequence_names(schema=schema))
        )
        exclusions = await conn.scalar(
            text(
                "SELECT count(*) FROM pg_constraint "
                "WHERE contype = 'x' AND connamespace = CAST(:schema AS regnamespace)"
            ),
            {"schema": schema},
        )
        triggers = set(
            await conn.scalars(
                text(
                    "SELECT tgname FROM pg_trigger "
                    "WHERE NOT tgisinternal AND tgrelid = CAST(:table AS regclass)"
                ),
                {"table": f"{schema}.reservations"},
            )
        )
        placeholder = await conn.scalar(
            text("SELECT count(*) FROM pg_namespace WHERE nspname = :name"), {"name": MERCHANT}
        )

    assert tables == MERCHANT_TABLES
    assert "reservation_seq" in sequences
    assert exclusions == 1
    assert triggers == {"reservations_notify", "reservations_skip_noop"}
    assert placeholder == 0


async def test_each_merchant_only_sees_its_own_rows(
    engine: AsyncEngine, two_merchants: tuple[str, str]
) -> None:
    zinc, mimi = two_merchants
    async with merchant_conn(engine, zinc) as conn:
        await conn.execute(
            insert(tables_t), [{"label": "Z1", "seats": 2}, {"label": "Z2", "seats": 4}]
        )
    async with merchant_conn(engine, mimi) as conn:
        await conn.execute(insert(tables_t), [{"label": "M1", "seats": 6}])

    async with merchant_conn(engine, zinc) as conn:
        zinc_labels = (
            await conn.scalars(select(tables_t.c.label).order_by(tables_t.c.label))
        ).all()
    async with merchant_conn(engine, mimi) as conn:
        mimi_labels = (await conn.scalars(select(tables_t.c.label))).all()

    assert zinc_labels == ["Z1", "Z2"]
    assert mimi_labels == ["M1"]


async def test_reservation_ids_follow_each_merchant_sequence(
    engine: AsyncEngine, two_merchants: tuple[str, str]
) -> None:
    zinc, mimi = two_merchants
    async with merchant_conn(engine, zinc) as conn:
        zinc_ids = [await next_reservation_id(conn), await next_reservation_id(conn)]
    async with merchant_conn(engine, mimi) as conn:
        mimi_id = await next_reservation_id(conn)

    assert zinc_ids == ["R-001", "R-002"]
    assert mimi_id == "R-001"


async def test_merchant_conn_rolls_back_on_error(
    engine: AsyncEngine, two_merchants: tuple[str, str]
) -> None:
    zinc = two_merchants[0]
    with pytest.raises(RuntimeError, match="boom"):
        async with merchant_conn(engine, zinc) as conn:
            await conn.execute(insert(tables_t), [{"label": "Z1", "seats": 2}])
            raise RuntimeError("boom")

    async with merchant_conn(engine, zinc) as conn:
        assert (await conn.scalars(select(tables_t.c.label))).all() == []


async def add_table(engine: AsyncEngine, slug: str) -> int:
    async with merchant_conn(engine, slug) as conn:
        table_id: int = await conn.scalar(
            insert(tables_t).values(label="T1", seats=2).returning(tables_t.c.id)
        )
    return table_id


def two_hours_from(start: datetime) -> Range[datetime]:
    return Range(start, start + timedelta(hours=2))


def booking(reservation_id: str, table_id: int, **overrides: Any) -> dict[str, Any]:
    """A confirmed booking via Claude, 8 pm to 10 pm."""
    return {
        "id": reservation_id,
        "table_id": table_id,
        "guest_name": "Ada",
        "party_size": 2,
        "during": two_hours_from(EIGHT_PM),
        "via": "claude",
        **overrides,
    }


@pytest.mark.parametrize(
    ("during", "expected_sqlstate"),
    [
        (two_hours_from(EIGHT_PM + timedelta(hours=1)), "23P01"),  # exclusion_violation
        (Range(empty=True), "23514"),  # check_violation: an empty range overlaps nothing
    ],
    ids=["overlap", "empty-range"],
)
async def test_reservations_reject_double_booking(
    engine: AsyncEngine,
    two_merchants: tuple[str, str],
    during: Range[datetime],
    expected_sqlstate: str,
) -> None:
    zinc = two_merchants[0]
    table_id = await add_table(engine, zinc)
    async with merchant_conn(engine, zinc) as conn:
        await conn.execute(insert(reservations_t).values(booking("R-001", table_id)))

    with pytest.raises(IntegrityError) as raised:
        async with merchant_conn(engine, zinc) as conn:
            await conn.execute(
                insert(reservations_t).values(booking("R-002", table_id, during=during))
            )

    assert sqlstate(raised.value) == expected_sqlstate


async def test_cancelled_reservations_free_the_table(
    engine: AsyncEngine, two_merchants: tuple[str, str]
) -> None:
    zinc = two_merchants[0]
    table_id = await add_table(engine, zinc)
    async with merchant_conn(engine, zinc) as conn:
        await conn.execute(
            insert(reservations_t).values(booking("R-001", table_id, status="cancelled"))
        )
        await conn.execute(insert(reservations_t).values(booking("R-002", table_id)))
        confirmed = (
            await conn.scalars(
                select(reservations_t.c.id).where(reservations_t.c.status == "confirmed")
            )
        ).all()

    assert confirmed == ["R-002"]


async def test_a_merchant_operation_marks_its_notifications(
    engine: AsyncEngine, libpq_database_url: str, two_merchants: tuple[str, str]
) -> None:
    """#134: `as_merchant` is transaction-local, so the next write is nobody's again."""
    zinc = two_merchants[0]
    table_id = await add_table(engine, zinc)
    async with await psycopg.AsyncConnection.connect(
        libpq_database_url, autocommit=True
    ) as listener:
        await listener.execute("LISTEN reservations")
        async with merchant_conn(engine, zinc) as conn:
            await as_merchant(conn)
            await conn.execute(insert(reservations_t).values(booking("R-001", table_id)))
        async with merchant_conn(engine, zinc) as conn:
            await conn.execute(
                update(reservations_t)
                .where(reservations_t.c.id == "R-001")
                .values(guest_name="Kim Weber")
            )
        received = [note async for note in listener.notifies(timeout=5, stop_after=2)]

    assert [json.loads(note.payload)["actor"] for note in received] == ["merchant", None]


async def test_reservation_writes_notify_the_reservations_channel(
    engine: AsyncEngine, libpq_database_url: str, two_merchants: tuple[str, str]
) -> None:
    zinc = two_merchants[0]
    table_id = await add_table(engine, zinc)
    async with await psycopg.AsyncConnection.connect(
        libpq_database_url, autocommit=True
    ) as listener:
        await listener.execute("LISTEN reservations")
        async with merchant_conn(engine, zinc) as conn:
            await conn.execute(insert(reservations_t).values(booking("R-001", table_id)))
        received = [note async for note in listener.notifies(timeout=5, stop_after=1)]

    assert [json.loads(note.payload) for note in received] == [
        {
            "schema": schema_for(zinc),
            "op": "INSERT",
            "id": "R-001",
            "status": "confirmed",
            "via": "claude",
            "actor": None,  # outside merchant operations (#134)
        }
    ]
