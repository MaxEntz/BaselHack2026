"""Studio's Send buttons (#151): real frames through a real node, from the platform's route."""

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.provisioning import deploy
from agent.service import PgAgentService
from batp.messages import Hello
from batp.server import NodeServer
from core.config import Settings
from core.models import AgentTryOutput
from merchant_node.__main__ import NODE
from merchant_node.agents import MerchantAgents
from merchant_platform.agent_try import NO_BOOKING, STUDIO_AGENT
from merchant_platform.api import create_app
from merchant_platform.trial import TEST_GUEST

pytestmark = pytest.mark.integration

MerchantFactory = Callable[..., Awaitable[str]]  # the `make_merchant` fixture
TZ = ZoneInfo("Europe/Zurich")
TOKEN = "studio-secret-token-1234"


@pytest.fixture
def service(engine: AsyncEngine) -> PgAgentService:
    return PgAgentService(engine, tz=TZ)


@pytest.fixture
async def node(engine: AsyncEngine, service: PgAgentService) -> AsyncIterator[NodeServer]:
    def only_studio(hello: Hello) -> bool:
        return hello.agent.id == STUDIO_AGENT.id and hello.token == TOKEN

    agents = MerchantAgents(engine, service)
    server = NodeServer(
        node=NODE, handle=agents, authenticate=only_studio, fallback_url=agents.fallback_url
    )
    await server.start("127.0.0.1", 0)
    yield server
    await server.stop()


def page(service: PgAgentService, owner: str, batp_url: str | None) -> httpx.AsyncClient:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url="postgresql+psycopg://unused@localhost/unused",
        platform_merchant=owner,
        batp_url=batp_url,
        batp_token=TOKEN,
    )
    app = create_app(settings, agent=service)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def send(http: httpx.AsyncClient, slug: str, intent: str) -> AgentTryOutput:
    response = await http.post(f"/merchants/{slug}/agent/try", json={"intent": intent})
    assert response.status_code == 200, response.text
    return AgentTryOutput.model_validate(response.json())


async def test_a_hidden_agent_answers_404_and_a_published_one_200(
    engine: AsyncEngine,
    service: PgAgentService,
    node: NodeServer,
    make_merchant: MerchantFactory,
) -> None:
    slug = await make_merchant(live=False)
    async with page(service, slug, f"batp://127.0.0.1:{node.port}") as http:
        hidden = await send(http, slug, "AVAILABILITY")
        assert hidden.status == 404
        assert json.loads(hidden.request)["merchant"] == slug
        assert json.loads(hidden.response)["status"] == 404

        await deploy(slug, engine=engine)

        published = await send(http, slug, "AVAILABILITY")
    assert published.status == 200
    assert "slots" in json.loads(published.response)["result"]


async def test_booking_moving_and_cancelling_are_real(
    service: PgAgentService, node: NodeServer, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    async with page(service, slug, f"batp://127.0.0.1:{node.port}") as http:
        booked = await send(http, slug, "BOOK")
        assert booked.status == 200
        reservation = json.loads(booked.response)["result"]["reservation"]
        assert reservation["via"] == "claude"
        assert json.loads(booked.request)["params"]["guest_name"] == TEST_GUEST
        found = await service.list_reservations(slug, service_day(reservation["starts_at"]))
        assert [row.id for row in found] == [reservation["id"]]

        moved = await send(http, slug, "AMEND")
        assert moved.status == 200
        assert json.loads(moved.response)["result"]["reservation"]["party_size"] == 1

        cancelled = await send(http, slug, "CANCEL")
        assert cancelled.status == 200
        assert json.loads(cancelled.request)["params"]["reservation_id"] == reservation["id"]
        assert json.loads(cancelled.response)["result"]["reservation"]["status"] == "cancelled"

        # None left: the node answers what it answers to any unknown booking.
        again = await send(http, slug, "CANCEL")
    assert again.status == 404
    assert json.loads(again.request)["params"]["reservation_id"] == NO_BOOKING


def service_day(starts_at: str) -> date:
    return datetime.fromisoformat(starts_at).astimezone(TZ).date()


async def test_the_frames_that_name_no_merchant(
    service: PgAgentService, node: NodeServer, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    async with page(service, slug, f"batp://127.0.0.1:{node.port}") as http:
        ping = await send(http, slug, "PING")
        recall = await send(http, slug, "RECALL")
        discover = await send(http, slug, "DISCOVER")
    assert (ping.status, recall.status, discover.status) == (200, 200, 200)
    assert json.loads(ping.response)["result"]["pong"] is True
    assert TOKEN not in ping.request + ping.response


async def test_without_a_node_the_route_says_so(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    async with page(service, slug, None) as http:
        response = await http.post(f"/merchants/{slug}/agent/try", json={"intent": "PING"})
    assert response.status_code == 503
    assert response.json()["error"] == "agent_unavailable"


async def test_only_the_owner_can_send_and_only_known_intents(
    service: PgAgentService, node: NodeServer, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    async with page(service, slug, f"batp://127.0.0.1:{node.port}") as http:
        other = await http.post("/merchants/someone-else/agent/try", json={"intent": "PING"})
        unknown = await http.post(f"/merchants/{slug}/agent/try", json={"intent": "HELLO"})
    assert other.status_code == 403
    assert unknown.status_code == 422
