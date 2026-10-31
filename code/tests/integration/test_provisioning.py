"""Provisioning a MerchantProfile on a real Postgres 16."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from sqlalchemy import func, insert, select, text
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.schema import CreateSchema, DropSchema

from agent.provisioning import (
    MerchantRecord,
    SlugTaken,
    deploy,
    deprovision,
    provision,
    set_status,
)
from core.config import get_settings
from core.db import merchant_conn, merchants_t, policy_t, reservations_t, schema_for, tables_t
from core.errors import InvalidInput, NotFound
from core.models import MerchantProfile, TableSpec

pytestmark = pytest.mark.integration

EIGHT_PM = datetime(2026, 10, 31, 20, tzinfo=ZoneInfo("Europe/Zurich"))


@pytest.fixture
def profile(profile_data: dict[str, Any]) -> MerchantProfile:
    """Le Petit Zinc under a unique slug: the session database is shared between tests."""
    return MerchantProfile.model_validate({**profile_data, "slug": f"zinc-{uuid4().hex[:8]}"})


@pytest.fixture
async def provisioned(
    engine: AsyncEngine, profile: MerchantProfile
) -> AsyncIterator[MerchantRecord]:
    record = await provision(profile, engine=engine)
    yield record
    await deprovision(profile.slug, engine=engine)


async def schema_exists(engine: AsyncEngine, slug: str) -> bool:
    async with engine.connect() as conn:
        count = await conn.scalar(
            text("SELECT count(*) FROM pg_namespace WHERE nspname = :name"),
            {"name": schema_for(slug)},
        )
    return bool(count == 1)


async def registry_rows(engine: AsyncEngine, slug: str) -> int:
    async with engine.connect() as conn:
        count = await conn.scalar(
            select(func.count()).select_from(merchants_t).where(merchants_t.c.slug == slug)
        )
    return count or 0


async def test_provision_builds_the_merchant(
    engine: AsyncEngine, profile: MerchantProfile, provisioned: MerchantRecord
) -> None:
    async with merchant_conn(engine, profile.slug) as conn:
        tables = (
            await conn.execute(select(tables_t.c.label, tables_t.c.seats).order_by(tables_t.c.id))
        ).all()
        policy = (await conn.execute(select(policy_t))).one()
    async with engine.connect() as conn:
        registry = (
            await conn.execute(select(merchants_t).where(merchants_t.c.slug == profile.slug))
        ).one()

    assert provisioned == MerchantRecord(
        id=registry.id, slug=profile.slug, schema=schema_for(profile.slug), status="building"
    )
    assert [tuple(row) for row in tables] == [(t.label, t.seats) for t in profile.tables]
    assert (policy.max_party_size, policy.slot_length_min) == (6, 120)
    assert policy.services == [
        {"start": "12:00", "end": "14:00"},
        {"start": "19:00", "end": "22:00"},
    ]
    assert registry.status == "building"
    assert registry.name == "Le Petit Zinc"
    assert registry.city == "Mulhouse"
    assert registry.website == "http://localhost:8080/"
    assert registry.fallback_url == "http://localhost:8080/reservation.html"
    assert registry.tags == ["vegetarian", "gluten-free", "terrace", "kid-friendly"]
    assert registry.price_level == "€€"
    assert registry.profile == profile.model_dump(mode="json")


async def test_deploy_and_set_status_switch_the_registry_status(
    engine: AsyncEngine, profile: MerchantProfile, provisioned: MerchantRecord
) -> None:
    async def status() -> str | None:
        async with engine.connect() as conn:
            value: str | None = await conn.scalar(
                select(merchants_t.c.status).where(merchants_t.c.slug == profile.slug)
            )
        return value

    await deploy(profile.slug, engine=engine)
    assert await status() == "live"
    await set_status(profile.slug, "down", engine=engine)
    assert await status() == "down"
    await set_status(profile.slug, "live", engine=engine)
    assert await status() == "live"


async def test_status_of_an_unknown_merchant_raises_not_found(engine: AsyncEngine) -> None:
    with pytest.raises(NotFound, match="no-such-merchant"):
        await deploy("no-such-merchant", engine=engine)


async def test_set_status_refuses_an_unknown_status(
    engine: AsyncEngine, profile: MerchantProfile, provisioned: MerchantRecord
) -> None:
    with pytest.raises(InvalidInput, match="closed"):
        await set_status(profile.slug, "closed", engine=engine)  # type: ignore[arg-type]


async def test_provisioned_schema_rejects_overlapping_bookings(
    engine: AsyncEngine, profile: MerchantProfile, provisioned: MerchantRecord
) -> None:
    def booking(reservation_id: str, start: datetime) -> dict[str, Any]:
        return {
            "id": reservation_id,
            "table_id": 1,
            "guest_name": "Ada",
            "party_size": 2,
            "during": Range(start, start + timedelta(hours=2)),
            "via": "assistant",
        }

    async with merchant_conn(engine, profile.slug) as conn:
        await conn.execute(insert(reservations_t).values(booking("R-001", EIGHT_PM)))
    with pytest.raises(IntegrityError) as raised:
        async with merchant_conn(engine, profile.slug) as conn:
            await conn.execute(
                insert(reservations_t).values(booking("R-002", EIGHT_PM + timedelta(hours=1)))
            )

    assert getattr(raised.value.orig, "sqlstate", None) == "23P01"


async def test_provisioned_schema_notifies_new_reservations(
    engine: AsyncEngine,
    libpq_database_url: str,
    profile: MerchantProfile,
    provisioned: MerchantRecord,
) -> None:
    async with await psycopg.AsyncConnection.connect(
        libpq_database_url, autocommit=True
    ) as listener:
        await listener.execute("LISTEN reservations")
        async with merchant_conn(engine, profile.slug) as conn:
            await conn.execute(
                insert(reservations_t).values(
                    id="R-001",
                    table_id=1,
                    guest_name="Ada",
                    party_size=2,
                    during=Range(EIGHT_PM, EIGHT_PM + timedelta(hours=2)),
                    via="assistant",
                )
            )
        received = [note async for note in listener.notifies(timeout=5, stop_after=1)]

    assert [json.loads(note.payload) for note in received] == [
        {
            "schema": provisioned.schema,
            "op": "INSERT",
            "id": "R-001",
            "status": "confirmed",
            "via": "assistant",
        }
    ]


# model_copy skips validation, so these values only fail in the database: a duplicate table
# label at the tables insert, a NUL byte in the name at the registry insert (the last step).
@pytest.mark.parametrize(
    "update",
    [
        {"tables": (TableSpec(label="T1", seats=2), TableSpec(label="T1", seats=4))},
        {"name": "Le Petit\x00Zinc"},
    ],
    ids=["tables-step", "registry-step"],
)
async def test_failure_midway_leaves_nothing_behind(
    engine: AsyncEngine, profile: MerchantProfile, update: dict[str, Any]
) -> None:
    with pytest.raises(DBAPIError):
        await provision(profile.model_copy(update=update), engine=engine)

    assert not await schema_exists(engine, profile.slug)
    assert await registry_rows(engine, profile.slug) == 0


async def test_provisioning_the_same_slug_twice_is_refused(
    engine: AsyncEngine, profile: MerchantProfile, provisioned: MerchantRecord
) -> None:
    with pytest.raises(SlugTaken, match="already provisioned"):
        await provision(profile, engine=engine)

    assert await registry_rows(engine, profile.slug) == 1


async def test_a_leftover_schema_counts_as_taken(
    engine: AsyncEngine, profile: MerchantProfile
) -> None:
    schema = schema_for(profile.slug)
    async with engine.begin() as conn:
        await conn.execute(CreateSchema(schema))
    try:
        with pytest.raises(SlugTaken):
            await provision(profile, engine=engine)
    finally:
        async with engine.begin() as conn:
            await conn.execute(DropSchema(schema, cascade=True))


async def test_concurrent_provisioning_of_one_slug_lets_exactly_one_win(
    engine: AsyncEngine, profile: MerchantProfile
) -> None:
    results = await asyncio.gather(
        provision(profile, engine=engine), provision(profile, engine=engine), return_exceptions=True
    )
    await deprovision(profile.slug, engine=engine)

    assert sorted(type(result).__name__ for result in results) == ["MerchantRecord", "SlugTaken"]


async def test_deprovision_removes_schema_and_registry_row(
    engine: AsyncEngine, profile: MerchantProfile
) -> None:
    await provision(profile, engine=engine)

    assert await deprovision(profile.slug, engine=engine) is True
    assert not await schema_exists(engine, profile.slug)
    assert await registry_rows(engine, profile.slug) == 0
    assert await deprovision(profile.slug, engine=engine) is False


async def test_without_an_engine_the_functions_use_database_url(
    engine: AsyncEngine,
    database_url: str,
    profile: MerchantProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # How the #10 scripts call them: no engine, DATABASE_URL from the environment.
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    try:
        await provision(profile)
        await deploy(profile.slug)
        await set_status(profile.slug, "down")
        async with engine.connect() as conn:
            status = await conn.scalar(
                select(merchants_t.c.status).where(merchants_t.c.slug == profile.slug)
            )
        removed = await deprovision(profile.slug)
    finally:
        get_settings.cache_clear()

    assert status == "down"
    assert removed is True
    assert await registry_rows(engine, profile.slug) == 0
