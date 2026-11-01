"""`talk_to_merchant` end to end (#251): a real MCP client, the gateway, and a BATP node.

The node is a `NodeServer` whose `handle` plays the merchant's agent from a script, so these
tests don't wait for the real one (#249): a question, a proposal, the `confirm` after the yes,
the booking. The registry is the e2e database, seeded with the 3 demo merchants.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from mcp import Client
from sqlalchemy.ext.asyncio import AsyncEngine

from batp.messages import AgentIdentity, Hello, NodeIdentity, Request
from batp.server import NodeServer
from core.config import Settings
from core.errors import NotFound
from tests.e2e.conftest import LOOPBACK, free_port, running_gateway

pytestmark = pytest.mark.integration

ZINC = "le-petit-zinc"
ZINC_FALLBACK = "http://localhost:8080/reservation.html"
TZ = ZoneInfo("Europe/Zurich")
GATEWAY_AGENT = "urn:batp:agent:claude-gateway"
NODE = NodeIdentity(id="urn:batp:node:scripted", name="Scripted merchant agent")
# The gateway's budget for a talk: the script's slow answer goes past it.
TALK_TIMEOUT_MS = 1500
SLOW = "Take your time."
PENDING_ID = "p-1"


def tonight_at(hhmm: str) -> datetime:
    hour, minute = map(int, hhmm.split(":"))
    return datetime.now(TZ).replace(hour=hour, minute=minute, second=0, microsecond=0)


class ScriptedAgent:
    """The merchant's agent as a script: what it answers depends on what Claude sends."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, Request]] = []

    async def __call__(self, agent: AgentIdentity, request: Request) -> dict[str, Any]:
        self.requests.append((agent.id, request))
        params = request.params
        if params.get("conversation") == "c-expired":
            raise NotFound("Unknown or expired conversation")
        if params["message"] == SLOW:
            await asyncio.sleep(TALK_TIMEOUT_MS / 1000 * 2)
        if params.get("confirm") == PENDING_ID:
            return self.booked()
        if "Alex" in params["message"]:
            return self.proposal()
        return self.answer()

    def answer(self) -> dict[str, Any]:
        slot = {"starts_at": tonight_at("20:00").isoformat(), "table_label": "T5"}
        return {
            "conversation": "c-1",
            "reply": "Yes, two vegetarian mains tonight. I can do 20:00. Which name?",
            "slots": [slot],
            "tools_called": ["get_availability"],
        }

    def proposal(self) -> dict[str, Any]:
        pending = {
            "id": PENDING_ID,
            "action": "book",
            "summary": "Table for 4 tonight at 20:00, under Alex Martin.",
            "expires_at": (datetime.now(TZ) + timedelta(minutes=10)).isoformat(),
        }
        return {"conversation": "c-1", "reply": "Shall I book it?", "pending": pending}

    def booked(self) -> dict[str, Any]:
        reservation = {
            "id": "R-042",
            "merchant": ZINC,
            "starts_at": tonight_at("20:00").isoformat(),
            "party_size": 4,
            "table_label": "T5",
            "status": "confirmed",
            "guest_name": "Alex Martin",
            "via": "claude",
        }
        return {
            "conversation": "c-1",
            "reply": "Booked: R-042.",
            "reservation": reservation,
            # A `confirm` is written by code before any model call (#247), and named by the
            # service call it made.
            "tools_called": ["create_reservation"],
        }


def accept_anyone(_: Hello) -> bool:
    return True


@pytest.fixture(scope="module")
async def scripted() -> AsyncIterator[tuple[ScriptedAgent, int]]:
    script = ScriptedAgent()
    node = NodeServer(node=NODE, handle=script, authenticate=accept_anyone)
    await node.start(LOOPBACK, 0)
    yield script, node.port
    await node.stop()


def talking_gateway(database_url: str, port: int) -> Settings:
    return Settings(
        _env_file=None,
        database_url=database_url,
        batp_url=f"batp://{LOOPBACK}:{port}",
        agent_talk_timeout_ms=TALK_TIMEOUT_MS,
    )


@pytest.fixture(scope="module")
def gateway_url(
    scripted: tuple[ScriptedAgent, int], e2e_database_url: str, e2e_engine: AsyncEngine
) -> Iterator[str]:
    _, port = scripted
    with running_gateway(talking_gateway(e2e_database_url, port)) as url:
        yield url


async def talk(client: Client, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(
        "talk_to_merchant", {"idempotency_key": str(uuid4()), **arguments}
    )
    assert not result.is_error
    assert result.structured_content is not None
    content: dict[str, Any] = result.structured_content["result"]
    return content


async def test_a_question_then_a_proposal_then_a_booking_after_the_yes(
    gateway_url: str, scripted: tuple[ScriptedAgent, int]
) -> None:
    script, _ = scripted
    script.requests.clear()
    async with Client(f"{gateway_url}/mcp") as client:
        answer = await talk(
            client, merchant=ZINC, message="Table for 4 tonight around 8pm? Vegetarian dishes?"
        )
        proposal = await talk(
            client,
            merchant=ZINC,
            message="20:00, Alex Martin.",
            conversation=answer["conversation"],
        )
        booked = await talk(
            client,
            merchant=ZINC,
            message="Yes, book it.",
            conversation=proposal["conversation"],
            confirm=proposal["pending"]["id"],
        )

    assert answer["slots"][0]["table_label"] == "T5"
    assert answer["pending"] is None
    assert proposal["pending"]["summary"].startswith("Table for 4")
    assert booked["reservation"]["id"] == "R-042"
    assert [(agent, request.intent, request.merchant) for agent, request in script.requests] == [
        (GATEWAY_AGENT, "TALK", ZINC)
    ] * 3
    # `merchant` travels outside the params, and what Claude didn't set isn't sent.
    first, _, last = (request.params for _, request in script.requests)
    assert first.keys() == {"message", "idempotency_key"}
    assert last["confirm"] == PENDING_ID


async def test_each_merchant_s_endpoint_talks_to_its_own_agent(
    gateway_url: str, scripted: tuple[ScriptedAgent, int]
) -> None:
    script, _ = scripted
    script.requests.clear()
    async with Client(f"{gateway_url}/m/{ZINC}/mcp") as client:
        (tool,) = (await client.list_tools()).tools
        answer = await talk(client, message="Do you have a table tonight?")

    assert tool.name == "talk_to_merchant"
    assert (tool.description or "").startswith("Talk to the agent of Le Petit Zinc (Mulhouse).")
    assert answer["reply"].startswith("Yes")
    ((_, request),) = script.requests
    assert request.merchant == ZINC


async def test_an_agent_over_its_deadline_gives_the_fallback_url(gateway_url: str) -> None:
    async with Client(f"{gateway_url}/mcp") as client:
        answer = await talk(client, merchant=ZINC, message=SLOW)

    assert answer["error"] == "agent_unavailable"
    assert answer["fallback_url"] == ZINC_FALLBACK


async def test_an_expired_conversation_keeps_its_code(gateway_url: str) -> None:
    async with Client(f"{gateway_url}/mcp") as client:
        answer = await talk(client, merchant=ZINC, message="Still there?", conversation="c-expired")

    assert answer["error"] == "not_found"
    assert answer.get("fallback_url") is None


async def test_a_node_that_is_down_gives_the_fallback_url(
    e2e_database_url: str, e2e_engine: AsyncEngine
) -> None:
    with running_gateway(talking_gateway(e2e_database_url, free_port())) as url:
        async with Client(f"{url}/mcp") as client:
            answer = await talk(client, merchant=ZINC, message="Table for 2?")

    assert answer["error"] == "agent_unavailable"
    assert answer["fallback_url"] == ZINC_FALLBACK
