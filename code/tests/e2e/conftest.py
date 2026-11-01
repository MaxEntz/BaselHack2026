"""The real gateway on a free port, over its own database seeded with the 3 demo merchants.

Every e2e scenario runs twice: with the agent in the gateway's process, and with the gateway as
a bridge to a real BATP node (#61).
"""

import asyncio
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import seed
import uvicorn
from mcp import Client
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.types import ASGIApp

from core.config import Settings
from core.db import make_engine
from gateway.server import create_app
from merchant_platform.events import LiveBus

# A process importing everything for the first time, right after `uv sync`, can take a while.
STARTUP_SECONDS = 30.0
LOOPBACK = "127.0.0.1"
TZ = ZoneInfo("Europe/Zurich")


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


class BatpNode:
    """`python -m merchant_node` as a separate process, as on the server: it can die or freeze."""

    def __init__(self, database_url: str, log_path: Path, tokens: str | None = None) -> None:
        with socket.socket() as probe:
            probe.bind((LOOPBACK, 0))
            self.port = probe.getsockname()[1]
        self.url = f"batp://{LOOPBACK}:{self.port}"
        self.database_url = database_url
        self.log_path = log_path
        self.tokens = tokens
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        env = {
            **os.environ,
            "DATABASE_URL": self.database_url,
            "BATP_HOST": LOOPBACK,
            "BATP_PORT": str(self.port),
            "BATP_TOKENS": self.tokens or "",
        }
        with self.log_path.open("ab") as log:
            self.process = subprocess.Popen(
                [sys.executable, "-m", "merchant_node"], env=env, stderr=log, stdout=log
            )
        deadline = time.monotonic() + STARTUP_SECONDS
        while True:
            assert self.process.poll() is None, f"node exited:\n{self.logs()}"
            try:
                socket.create_connection((LOOPBACK, self.port), timeout=0.2).close()
                return
            except OSError:
                assert time.monotonic() < deadline, f"node did not start:\n{self.logs()}"
                time.sleep(0.02)

    def stop(self) -> None:
        assert self.process is not None
        self.process.send_signal(signal.SIGCONT)
        self.process.terminate()
        self.process.wait(timeout=STARTUP_SECONDS)

    def freeze(self) -> None:
        """The kernel still accepts connections, but nothing answers: a slow node."""
        assert self.process is not None
        self.process.send_signal(signal.SIGSTOP)

    def resume(self) -> None:
        assert self.process is not None
        self.process.send_signal(signal.SIGCONT)

    def logs(self) -> str:
        return self.log_path.read_text()


@pytest.fixture(scope="module")
def batp_node(
    e2e_database_url: str, e2e_engine: AsyncEngine, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[BatpNode]:
    node = BatpNode(e2e_database_url, tmp_path_factory.mktemp("batp") / "node.log")
    node.start()
    yield node
    if node.process and node.process.poll() is None:
        node.stop()


def free_port() -> int:
    """A port nothing listens on, for a server whose address must be known before it starts."""
    with socket.socket() as probe:
        probe.bind((LOOPBACK, 0))
        port: int = probe.getsockname()[1]
        return port


@contextmanager
def serving(app: ASGIApp, port: int = 0) -> Iterator[str]:
    """`app` on uvicorn in a thread, with its own event loop and its lifespan."""
    config = uvicorn.Config(app, host=LOOPBACK, port=port, log_config=None)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + STARTUP_SECONDS
    while not server.started:
        assert thread.is_alive() and time.monotonic() < deadline, "server did not start"
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://{LOOPBACK}:{port}"
    server.should_exit = True
    thread.join(timeout=STARTUP_SECONDS)


@contextmanager
def running_gateway(settings: Settings) -> Iterator[str]:
    """`python -m gateway`, minus the port."""
    with serving(create_app(settings)) as url:
        yield url


@pytest.fixture(scope="module", params=["in_process", "over_batp"])
def gateway_base_url(
    request: pytest.FixtureRequest, e2e_database_url: str, e2e_engine: AsyncEngine
) -> Iterator[str]:
    batp_url = request.getfixturevalue("batp_node").url if request.param == "over_batp" else None
    settings = Settings(_env_file=None, database_url=e2e_database_url, batp_url=batp_url)
    with running_gateway(settings) as url:
        yield url


@pytest.fixture(scope="module")
def bridge_base_url(batp_node: BatpNode, e2e_database_url: str) -> Iterator[str]:
    settings = Settings(_env_file=None, database_url=e2e_database_url, batp_url=batp_node.url)
    with running_gateway(settings) as url:
        yield url


async def subscribed(bus: LiveBus, slug: str) -> None:
    """Until a client listens to `slug`'s live events. An open stream is no sign: it sends its
    headers before it subscribes to the bus (#216)."""
    deadline = time.monotonic() + 5
    while bus.subscriber_count(slug) == 0:
        assert time.monotonic() < deadline, f"nothing listens to {slug}'s live events"
        await asyncio.sleep(0.05)


def tomorrow() -> date:
    """Always bookable: the clock is real, and the service refuses past times."""
    return datetime.now(TZ).date() + timedelta(days=1)


async def call(gateway: str, tool: str, **arguments: Any) -> dict[str, Any]:
    """What Claude gets from one of the gateway's tools: failures are results too."""
    async with Client(f"{gateway}/mcp") as client:
        result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    content: dict[str, Any] = result.structured_content["result"]
    return content


async def offered(gateway: str, slug: str, day: date, hhmm: str, party_size: int = 2) -> list[str]:
    """The times the agent offers `party_size` around `hhmm`."""
    answer = await call(
        gateway,
        "get_availability",
        merchant=slug,
        date=day.isoformat(),
        time=hhmm,
        party_size=party_size,
    )
    assert "error" not in answer, answer
    return [
        datetime.fromisoformat(slot["starts_at"]).astimezone(TZ).strftime("%H:%M")
        for slot in answer["slots"]
    ]
