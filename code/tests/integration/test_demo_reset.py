"""demo_reset on a real Postgres (#10): any database ends migrated, with no merchant left."""

import asyncio

import demo_reset
import pytest
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.schema import CreateSchema

from core.db import (
    guest_notices_t,
    make_engine,
    merchant_conn,
    merchant_metadata,
    merchants_t,
    schema_for,
)
from core.models import MerchantProfile

pytestmark = pytest.mark.integration


def registry_row(profile: MerchantProfile) -> dict[str, object]:
    return {
        "slug": profile.slug,
        "name": profile.name,
        "website": str(profile.website),
        "fallback_url": str(profile.fallback_url),
        "price_level": profile.price_level,
        "profile": profile.model_dump(mode="json"),
    }


async def populate(engine: AsyncEngine, profiles: list[MerchantProfile]) -> None:
    """Merchant schemas built like provisioning (#7), registry rows, a notice, another schema."""
    for profile in profiles:
        async with merchant_conn(engine, profile.slug) as conn:
            await conn.execute(CreateSchema(schema_for(profile.slug)))
            await conn.run_sync(merchant_metadata.create_all)
    async with engine.begin() as conn:
        await conn.execute(insert(merchants_t), [registry_row(p) for p in profiles])
        await conn.execute(
            insert(guest_notices_t).values(
                guest_name="Ada", merchant_slug=profiles[0].slug, reservation_id="R-1", message="x"
            )
        )
        await conn.execute(text("CREATE SCHEMA mx_other"))  # LIKE 'm_%' would match it


async def state(engine: AsyncEngine, profile: MerchantProfile) -> tuple[set[str], int, int]:
    """Schemas, registry size, and the id the next guest notice gets."""
    async with engine.begin() as conn:
        schemas = set(await conn.scalars(text("SELECT nspname FROM pg_namespace")))
        merchants = await conn.scalar(text("SELECT count(*) FROM public.merchants"))
        await conn.execute(insert(merchants_t).values(registry_row(profile)))
        notice_id = await conn.scalar(
            insert(guest_notices_t)
            .values(guest_name="Ada", merchant_slug=profile.slug, reservation_id="R-1", message="x")
            .returning(guest_notices_t.c.id)
        )
    return schemas, merchants or 0, notice_id or 0


def test_any_database_ends_migrated_without_merchants(
    empty_database_url: str, demo_profiles: dict[str, MerchantProfile]
) -> None:
    # Sync on purpose: migrations run asyncio.run() (env.py), which fails inside a running loop.
    url = empty_database_url
    profiles = [demo_profiles["le-petit-zinc"], demo_profiles["maison-albane"]]

    async def seed_then_reset() -> tuple[list[str], list[str], tuple[set[str], int, int]]:
        engine = make_engine(url)
        try:
            await populate(engine, profiles)
            dropped = await demo_reset.reset(url)
            dropped_again = await demo_reset.reset(url)
            return dropped, dropped_again, await state(engine, demo_profiles["la-graine-verte"])
        finally:
            await engine.dispose()

    assert demo_reset.is_local(url)
    demo_reset.migrate(url)  # fresh, unmigrated database
    demo_reset.migrate(url)  # already at head: nothing to do
    dropped, dropped_again, (schemas, merchants, notice_id) = asyncio.run(seed_then_reset())

    assert dropped == ["m_le_petit_zinc", "m_maison_albane"]
    assert dropped_again == []
    assert {"public", "mx_other"} <= schemas
    assert not {"m_le_petit_zinc", "m_maison_albane"} & schemas
    assert merchants == 0
    assert notice_id == 1  # RESTART IDENTITY
