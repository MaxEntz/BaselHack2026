"""The real gateway on a free port, over its own database seeded with the 3 demo merchants.

`gateway_base_url` runs a test twice: with the gateway's own process answering, and with the
gateway as a bridge to `python -m merchant_node` (#61). Neither has a model: the search and the
guest's bookings answer, `talk_to_merchant` gets the fallback URL. Only the merchant's agent
books (#254): `talking_gateway` is bridged to a real node whose agent runs on a scripted model.
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
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
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
from tests.integration.agent_fakes import TOKEN, Node, ScriptedLLM, calls, says
from tests.integration.agent_fakes import call as tool_call

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
    """`python -m merchant_node` as a separate process, as on the server: it can die or freeze.

    It never has a model: no `OPENAI_API_KEY`, and it runs outside `code/`, away from `.env`.
    """

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
            **{name: value for name, value in os.environ.items() if name != "OPENAI_API_KEY"},
            "DATABASE_URL": self.database_url,
            "BATP_HOST": LOOPBACK,
            "BATP_PORT": str(self.port),
            "BATP_TOKENS": self.tokens or "",
        }
        with self.log_path.open("ab") as log:
            self.process = subprocess.Popen(
                [sys.executable, "-m", "merchant_node"],
                env=env,
                cwd=self.log_path.parent,
                stderr=log,
                stdout=log,
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
    """The gateway on its own, then bridged to `python -m merchant_node`: no model in either."""
    batp_url = request.getfixturevalue("batp_node").url if request.param == "over_batp" else None
    settings = Settings(_env_file=None, database_url=e2e_database_url, batp_url=batp_url)
    with running_gateway(settings) as url:
        yield url


@pytest.fixture(scope="module")
def bridge_base_url(batp_node: BatpNode, e2e_database_url: str) -> Iterator[str]:
    settings = Settings(_env_file=None, database_url=e2e_database_url, batp_url=batp_node.url)
    with running_gateway(settings) as url:
        yield url


@dataclass(frozen=True)
class TalkingNode:
    """A real BATP node in this process: the merchants' agents on a scripted model, the real
    clock, the real service and request log (`tests.integration.agent_fakes.Node`)."""

    url: str
    llm: ScriptedLLM


@asynccontextmanager
async def node_talking_on(engine: AsyncEngine) -> AsyncIterator[TalkingNode]:
    """A `TalkingNode` over `engine`'s database, on a free port."""
    llm = ScriptedLLM()
    node = Node(engine, llm, clock=lambda: datetime.now(TZ))
    await node.server.start(LOOPBACK, 0)
    try:
        yield TalkingNode(f"batp://{LOOPBACK}:{node.server.port}", llm)
    finally:
        await node.server.stop()


@pytest.fixture(scope="module")
async def talking_node(e2e_engine: AsyncEngine) -> AsyncIterator[TalkingNode]:
    async with node_talking_on(e2e_engine) as node:
        yield node


def bridged_to(node: TalkingNode, database_url: str, **settings: Any) -> Settings:
    """A gateway's settings as a bridge to `node`, with the token it expects."""
    return Settings(
        _env_file=None, database_url=database_url, batp_url=node.url, batp_token=TOKEN, **settings
    )


@pytest.fixture(scope="module")
def talking_gateway(talking_node: TalkingNode, e2e_database_url: str) -> Iterator[str]:
    with running_gateway(bridged_to(talking_node, e2e_database_url)) as url:
        yield url


@pytest.fixture
def agent_llm(talking_node: TalkingNode) -> Iterator[ScriptedLLM]:
    """The node's model with an empty script, for this test to write; it must use it all."""
    llm = talking_node.llm
    llm.replies.clear()
    llm.calls.clear()
    yield llm
    assert llm.replies == [], "the merchant agent's model didn't get every scripted call"


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


async def call_at(url: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """What Claude gets from a tool of the MCP endpoint at `url`: failures are results too."""
    async with Client(url) as client:
        result = await client.call_tool(tool, arguments)
    assert not result.is_error, result.content
    assert result.structured_content is not None
    content: dict[str, Any] = result.structured_content["result"]
    return content


async def call(gateway: str, tool: str, **arguments: Any) -> dict[str, Any]:
    """The same on the gateway's shared endpoint, `/mcp`."""
    return await call_at(f"{gateway}/mcp", tool, arguments)


async def talk(url: str, message: str, **arguments: Any) -> dict[str, Any]:
    """One `talk_to_merchant` on the endpoint at `url`, with a fresh key: on `/mcp`, `arguments`
    name the merchant; a merchant's own endpoint takes none."""
    key = str(uuid4())
    return await call_at(
        url, "talk_to_merchant", {"message": message, "idempotency_key": key, **arguments}
    )


def local_times(answer: dict[str, Any]) -> list[str]:
    """The `HH:MM` of a talk's `slots`, in the merchants' time zone."""
    return [
        datetime.fromisoformat(slot["starts_at"]).astimezone(TZ).strftime("%H:%M")
        for slot in answer["slots"]
    ]


async def offered(
    gateway: str, llm: ScriptedLLM, slug: str, day: date, hhmm: str, party_size: int = 2
) -> list[str]:
    """The times `slug`'s agent offers `party_size` around `hhmm`: its model asks the
    availability tool, and code fills `slots` from what the tool found."""
    llm.then(
        calls(
            tool_call("get_availability", date=day.isoformat(), time=hhmm, party_size=party_size)
        ),
        says("Here are the times I have."),
    )
    answer = await talk(
        f"{gateway}/mcp", f"A table for {party_size} around {hhmm} on {day}?", merchant=slug
    )
    assert answer.get("tools_called") == ["get_availability"], answer
    return local_times(answer)


async def book(
    gateway: str, llm: ScriptedLLM, slug: str, starts_at: datetime, guest: str, party_size: int = 2
) -> dict[str, Any]:
    """A booking as only the merchant's agent makes one (#254): its model proposes it, the guest
    says yes, and code books it on the `confirm`. The reservation."""
    llm.then(
        calls(
            tool_call(
                "propose_booking",
                starts_at=starts_at.isoformat(),
                party_size=party_size,
                guest_name=guest,
            )
        ),
        says("Shall I book it?"),
    )
    url = f"{gateway}/mcp"
    proposed = await talk(url, f"{party_size} at {starts_at:%H:%M}, {guest}.", merchant=slug)
    assert proposed.get("pending"), proposed
    done = await talk(
        url,
        "Yes, book it.",
        merchant=slug,
        conversation=proposed["conversation"],
        confirm=proposed["pending"]["id"],
    )
    assert done.get("reservation"), done
    reservation: dict[str, Any] = done["reservation"]
    return reservation
