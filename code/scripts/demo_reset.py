"""Put the demo database back into a known state (#10), whatever its current state.

Applies the migrations, drops every merchant schema (`m_*`), empties the registry and the guest
notices, then runs `scripts/seed.py` with the same options. Without options: demo mode, where
the onboarding builds Le Petit Zinc live. Refuses a DATABASE_URL that is not on localhost.

    uv run python scripts/demo_reset.py                           # = --except le-petit-zinc
    uv run python scripts/demo_reset.py --all --with-bookings
"""

import asyncio
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import seed
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.schema import DropSchema

from core.config import get_settings

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
DEMO_MODE = ["--except", "le-petit-zinc"]


def is_local(database_url: str) -> bool:
    url = make_url(database_url)
    # libpq also takes a host from the query string: `?host=…` must not get around the check.
    return url.host in LOCAL_HOSTS and not {"host", "hostaddr"} & set(url.query)


async def wipe(conn: AsyncConnection) -> list[str]:
    """Drop every merchant schema and empty the public tables; returns the dropped schemas."""
    # starts_with rather than LIKE 'm_%', where `_` is a wildcard that also matches "mx…".
    schemas = list(
        await conn.scalars(
            text("SELECT nspname FROM pg_namespace WHERE starts_with(nspname, 'm_') ORDER BY 1")
        )
    )
    for schema in schemas:
        await conn.execute(DropSchema(schema, cascade=True))
    await conn.execute(text("TRUNCATE public.guest_notices, public.merchants RESTART IDENTITY"))
    return schemas


async def reset(database_url: str) -> list[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            return await wipe(conn)
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv) or DEMO_MODE
    seed.parser().parse_args(argv)  # bad options fail before anything is dropped
    database_url = get_settings().database_url
    if not is_local(database_url):
        shown = make_url(database_url).render_as_string(hide_password=True)
        print(f"Refusing to reset {shown}: DATABASE_URL must point to localhost.", file=sys.stderr)
        return 2

    started = time.perf_counter()
    try:
        command.upgrade(Config(ALEMBIC_INI), "head")
        dropped = asyncio.run(reset(database_url))
    except DBAPIError as error:
        return seed.database_error(error)
    print(f"Dropped {len(dropped)} merchant schemas: {', '.join(dropped) or 'none'}")
    status = seed.main(argv)
    print(f"Demo reset in {time.perf_counter() - started:.1f} s")
    return status


if __name__ == "__main__":
    sys.exit(main())
