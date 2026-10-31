"""The real gateway on a free port, over its own database seeded with the 3 demo merchants."""

import threading
import time
from collections.abc import AsyncIterator, Iterator

import pytest
import seed
import uvicorn
from sqlalchemy.ext.asyncio import AsyncEngine

from core.config import Settings
from core.db import make_engine
from gateway.server import create_app

STARTUP_SECONDS = 10.0


@pytest.fixture(scope="module")
def e2e_database_url(migrated_scratch_database_url: str) -> str:
    """Not the session database, shared between tests: these tests count the merchants."""
    return migrated_scratch_database_url


@pytest.fixture(scope="module")
async def e2e_engine(e2e_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = make_engine(e2e_database_url)
    for slug in seed.MERCHANTS:
        await seed.seed_merchant(engine, seed.load_profile(slug))
    yield engine
    await engine.dispose()


@pytest.fixture(scope="module")
def gateway_base_url(e2e_database_url: str, e2e_engine: AsyncEngine) -> Iterator[str]:
    """`python -m gateway`, minus the port: uvicorn in a thread, with its own event loop."""
    settings = Settings(_env_file=None, database_url=e2e_database_url)
    config = uvicorn.Config(create_app(settings), host="127.0.0.1", port=0, log_config=None)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + STARTUP_SECONDS
    while not server.started:
        assert thread.is_alive() and time.monotonic() < deadline, "gateway did not start"
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=STARTUP_SECONDS)
