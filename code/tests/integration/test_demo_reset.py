"""demo_reset's wipe on a real Postgres: merchant schemas go, other schemas stay (#10)."""

from collections.abc import Iterator

import demo_reset
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.community.postgres import PostgresContainer

pytestmark = pytest.mark.integration

SETUP = (
    # Stand-ins for #6's public tables, with the same foreign key.
    "CREATE TABLE public.merchants (slug text PRIMARY KEY)",
    "CREATE TABLE public.guest_notices (id bigserial PRIMARY KEY,"
    " merchant_slug text NOT NULL REFERENCES public.merchants (slug) ON DELETE CASCADE)",
    "INSERT INTO public.merchants VALUES ('le-petit-zinc'), ('maison-albane')",
    "INSERT INTO public.guest_notices (merchant_slug) VALUES ('le-petit-zinc')",
    "CREATE SCHEMA m_le_petit_zinc",
    "CREATE TABLE m_le_petit_zinc.reservations (id text PRIMARY KEY)",
    "CREATE SCHEMA m_maison_albane",
    "CREATE SCHEMA mx_other",  # matched by LIKE 'm_%', where `_` is a wildcard
)


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer("postgres:16", driver="psycopg") as postgres:
        yield postgres.get_connection_url()


async def test_wipe_drops_merchant_schemas_and_empties_the_registry(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        for statement in SETUP:
            await conn.execute(text(statement))

    dropped = await demo_reset.reset(database_url)
    dropped_again = await demo_reset.reset(database_url)

    async with engine.begin() as conn:
        schemas = set(await conn.scalars(text("SELECT nspname FROM pg_namespace")))
        merchants = await conn.scalar(text("SELECT count(*) FROM public.merchants"))
        await conn.execute(text("INSERT INTO public.merchants VALUES ('la-graine-verte')"))
        notice_id = await conn.scalar(
            text(
                "INSERT INTO public.guest_notices (merchant_slug)"
                " VALUES ('la-graine-verte') RETURNING id"
            )
        )
    await engine.dispose()

    assert demo_reset.is_local(database_url)
    assert dropped == ["m_le_petit_zinc", "m_maison_albane"]
    assert dropped_again == []
    assert {"public", "mx_other"} <= schemas
    assert not {"m_le_petit_zinc", "m_maison_albane"} & schemas
    assert merchants == 0
    assert notice_id == 1  # RESTART IDENTITY
