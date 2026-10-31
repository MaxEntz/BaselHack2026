"""Postgres 16 in a container, migrated to head once per test session."""

import os
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.community.postgres import PostgresContainer

from core.db import make_engine

ALEMBIC_INI = Path(__file__).parents[2] / "alembic.ini"

# On macOS, Docker runs in a VM: the Ryuk cleanup container must mount the socket's path
# inside that VM, not the host path (~/.docker/run/docker.sock), or it fails to start.
# Read lazily by testcontainers, so setting it here is enough.
if sys.platform == "darwin":
    os.environ.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")


def alembic_config(url: str) -> Config:
    """Alembic config aimed at `url` (env.py keeps a URL that is already set)."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def libpq_url(url: str) -> str:
    """SQLAlchemy URL (`postgresql+psycopg://…`) → plain URL for psycopg.connect."""
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def postgres() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:16", driver="psycopg") as container:
        yield container


@pytest.fixture(scope="session")
def database_url(postgres: PostgresContainer) -> str:
    # Sync fixture on purpose: env.py calls asyncio.run(), which fails inside a running loop.
    url: str = postgres.get_connection_url()
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture(scope="session")
def libpq_database_url(database_url: str) -> str:
    """The migrated database, for raw psycopg connections (LISTEN)."""
    return libpq_url(database_url)


@pytest.fixture(scope="session")
def alembic_config_for() -> Callable[[str], Config]:
    return alembic_config


@pytest.fixture
def empty_database_url(postgres: PostgresContainer) -> Iterator[str]:
    """A fresh, unmigrated database in the same container, dropped afterwards."""
    url: str = postgres.get_connection_url()
    name = f"scratch_{uuid4().hex[:8]}"
    with psycopg.connect(libpq_url(url), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    yield make_url(url).set(database=name).render_as_string(hide_password=False)
    with psycopg.connect(libpq_url(url), autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture(scope="session")
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = make_engine(database_url)
    yield engine
    await engine.dispose()
