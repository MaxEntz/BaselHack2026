import json
import os
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.community.postgres import PostgresContainer

from core.db import make_engine
from core.models import MerchantProfile

FIXTURES = Path(__file__).parent / "fixtures"
ALEMBIC_INI = Path(__file__).parents[1] / "alembic.ini"
# The demo's fictional restaurants in Mulhouse (#10), in name order.
DEMO_MERCHANTS = ("la-graine-verte", "le-petit-zinc", "maison-albane")


# On macOS, Docker runs in a VM: the Ryuk cleanup container must mount the socket's path
# inside that VM, not the host path (~/.docker/run/docker.sock), or it fails to start.
# Read lazily by testcontainers, so setting it here is enough.
if sys.platform == "darwin":
    os.environ.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")


class _LlmEnv(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    openai_api_key: SecretStr | None = None


def _chromium_installed() -> bool:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        return Path(playwright.chromium.executable_path).exists()


@pytest.hookimpl(tryfirst=True)  # before `-m` deselects anything
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark `integration` every test that starts Postgres, through any fixture; skip `llm`
    tests when no OpenAI key is available (env or .env), `browser` tests when Playwright's
    Chromium is missing."""
    for item in items:
        if "postgres" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.integration)
    skips: dict[str, pytest.MarkDecorator] = {}
    if not _LlmEnv().openai_api_key:
        skips["llm"] = pytest.mark.skip(reason="OPENAI_API_KEY not set")
    if any("browser" in item.keywords for item in items) and not _chromium_installed():
        skips["browser"] = pytest.mark.skip(
            reason="no Chromium: uv run playwright install chromium"
        )
    for item in items:
        for marker, skip in skips.items():
            if marker in item.keywords:
                item.add_marker(skip)


@pytest.fixture
def profile_data() -> dict[str, Any]:
    path = FIXTURES / "le-petit-zinc.json"
    return json.loads(path.read_text())


@pytest.fixture
def demo_profiles() -> dict[str, MerchantProfile]:
    """The 3 demo merchants by slug, as `scripts/seed.py` provisions them."""
    return {
        slug: MerchantProfile.model_validate_json((FIXTURES / f"{slug}.json").read_text())
        for slug in DEMO_MERCHANTS
    }


def alembic_config(url: str) -> Config:
    """Alembic config aimed at `url` (env.py keeps a URL that is already set)."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def libpq_url(url: str) -> str:
    """SQLAlchemy URL (`postgresql+psycopg://…`) → plain URL for psycopg.connect."""
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


@contextmanager
def scratch_database(postgres: PostgresContainer) -> Iterator[str]:
    """A fresh, unmigrated database in the same container, dropped afterwards."""
    url: str = postgres.get_connection_url()
    name = f"scratch_{uuid4().hex[:8]}"
    with psycopg.connect(libpq_url(url), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    yield make_url(url).set(database=name).render_as_string(hide_password=False)
    with psycopg.connect(libpq_url(url), autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture(scope="session")
def postgres() -> Iterator[PostgresContainer]:
    """Postgres 16 in a container, started once per test session."""
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
    with scratch_database(postgres) as url:
        yield url


@pytest.fixture(scope="module")
def migrated_scratch_database_url(postgres: PostgresContainer) -> Iterator[str]:
    """A database of its own for one test module, unlike the shared session database."""
    with scratch_database(postgres) as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="session")
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = make_engine(database_url)
    yield engine
    await engine.dispose()
