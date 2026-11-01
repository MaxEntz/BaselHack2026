import logging
from typing import Any

import httpx
import pytest
from mcp import Client
from mcp.types import Tool
from pydantic import BaseModel
from starlette.testclient import TestClient

from core.config import Settings
from core.db import make_engine
from core.models import GetMyReservationsInput, SearchMerchantsInput, TalkInput
from gateway.agent_client import AgentClient
from gateway.server import INSTRUCTIONS, allowed_hosts, create_app, create_server

PUBLIC_URL = "https://merchant-agents.example.com"
LOCAL_URL = "http://localhost:8001"
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}
TOOL_INPUTS: dict[str, type[BaseModel]] = {
    "search_merchants": SearchMerchantsInput,
    "talk_to_merchant": TalkInput,
    "get_my_reservations": GetMyReservationsInput,
}
KEY = "0b6c3e1a-5f7d-4c2e-9a8b-1d2e3f405162"


def make_settings(**overrides: Any) -> Settings:
    values = {
        "database_url": "postgresql+psycopg://u:p@localhost:5432/db",
        "gateway_public_url": None,
        "gateway_token": None,
    }
    return Settings(_env_file=None, **{**values, **overrides})


def post_initialize(
    base_url: str, settings: Settings, headers: dict[str, Any] | None = None
) -> httpx.Response:
    with TestClient(create_app(settings), base_url=base_url) as client:
        return client.post("/mcp", json=INITIALIZE, headers={**MCP_HEADERS, **(headers or {})})


def make_client() -> Client:
    """In-process client. Tools aren't called: arguments are rejected before they run."""
    settings = make_settings()
    no_agent: AgentClient = NotImplemented
    return Client(create_server(no_agent, make_engine(settings.database_url), settings))


async def list_tools() -> dict[str, Tool]:
    async with make_client() as client:
        return {tool.name: tool for tool in (await client.list_tools()).tools}


async def test_exposes_the_three_user_tools_and_nothing_else() -> None:
    # Only the merchant's agent books (#254): no booking tool is left to call directly.
    assert list((await list_tools()).keys()) == list(TOOL_INPUTS)


@pytest.mark.parametrize("name", TOOL_INPUTS)
async def test_tool_arguments_follow_the_frozen_contract(name: str) -> None:
    schema = (await list_tools())[name].input_schema
    model = TOOL_INPUTS[name].model_fields

    assert schema["properties"].keys() == model.keys()
    assert set(schema["required"]) == {key for key, field in model.items() if field.is_required()}
    assert all(property_["description"] for property_ in schema["properties"].values())


async def test_the_talk_tells_claude_to_confirm_first() -> None:
    description = (await list_tools())["talk_to_merchant"].description or ""

    assert "explicit yes" in description
    # A user pre-confirming in their request still gets the summary first (#14).
    assert "in advance" in description
    # Never the `confirm` in the reply that got the proposal (#109).
    assert "never in the same reply as the call that returned the `pending`" in description
    assert "fallback_url" in description


async def test_search_tells_claude_to_pass_preferences_and_not_drop_them() -> None:
    description = (await list_tools())["search_merchants"].description or ""

    assert "before any web or map search" in description
    assert "ALWAYS pass the user's known preferences" in description
    assert "never drop a filter silently" in description
    assert "fallback_url" in description


async def test_get_my_reservations_tells_claude_to_relay_notices() -> None:
    description = (await list_tools())["get_my_reservations"].description or ""

    assert "relay notices" in description
    # A notice comes back for a day (#91): once per conversation, in both texts Claude reads.
    assert "24 hours" in description
    assert "don't repeat one whose text you already gave" in description
    assert "once per conversation" in INSTRUCTIONS


async def test_annotations_describe_each_tool() -> None:
    tools = await list_tools()

    def hints(name: str) -> tuple[bool | None, bool | None, bool | None]:
        annotations = tools[name].annotations
        assert annotations is not None
        return (
            annotations.read_only_hint,
            annotations.destructive_hint,
            annotations.idempotent_hint,
        )

    assert hints("search_merchants")[0] is True
    assert hints("get_my_reservations")[0] is True
    # A conversation may end in a cancellation; its key makes a resend harmless.
    assert hints("talk_to_merchant") == (False, True, True)


async def test_server_sends_its_rules_to_claude() -> None:
    async with make_client() as client:
        instructions = client.instructions or ""

    flat = " ".join(instructions.split())
    assert "explicit yes" in flat
    assert "in advance" in flat
    # The `confirm` never goes with the call that got the proposal (#109).
    assert "never send `confirm` in the same reply as the talk_to_merchant call" in flat
    assert "don't confirm another one" in flat
    assert "fallback_url" in flat


@pytest.mark.parametrize(
    ("arguments", "field"),
    [
        ({"merchant": "Le Petit Zinc", "message": "Hi", "idempotency_key": KEY}, "merchant"),
        ({"merchant": "le-petit-zinc", "message": "Hi", "idempotency_key": "k-1"}, "idempotency"),
        ({"merchant": "le-petit-zinc", "message": "", "idempotency_key": KEY}, "message"),
        ({"merchant": "le-petit-zinc", "idempotency_key": KEY}, "message"),
    ],
)
async def test_rejected_arguments_answer_invalid_input_and_are_logged(
    arguments: dict[str, Any], field: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="gateway.tools"):
        async with make_client() as client:
            result = await client.call_tool("talk_to_merchant", arguments)

    assert not result.is_error
    assert result.structured_content is not None
    error = result.structured_content["result"]
    assert error["error"] == "invalid_input"
    assert field in error["message"]
    assert "errors.pydantic.dev" not in error["message"]
    logged = [r.__dict__ for r in caplog.records if r.getMessage() == "tool_call"]
    assert [(r["tool"], r["outcome"]) for r in logged] == [("talk_to_merchant", "invalid_input")]


def test_health_is_degraded_without_database_and_needs_no_token() -> None:
    settings = make_settings(
        database_url="postgresql+psycopg://u:p@127.0.0.1:1/db",
        gateway_token="s3cret",
        agent_timeout_ms=500,
    )
    with TestClient(create_app(settings), base_url=LOCAL_URL) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unreachable"}


def test_health_is_outside_the_mcp_host_allowlist() -> None:
    settings = make_settings(
        database_url="postgresql+psycopg://u:p@127.0.0.1:1/db", agent_timeout_ms=500
    )
    with TestClient(create_app(settings), base_url="https://monitoring.example") as client:
        assert client.get("/health").status_code == 503


@pytest.mark.parametrize(
    "public_url",
    [PUBLIC_URL, "merchant-agents.example.com", f"{PUBLIC_URL}/mcp"],
)
def test_allowed_hosts_extracts_the_public_domain(public_url: str) -> None:
    hosts = allowed_hosts(public_url)

    assert "merchant-agents.example.com" in hosts
    assert "merchant-agents.example.com:*" in hosts


@pytest.mark.parametrize("public_url", ["https://", '"https://quoted.example"'])
def test_allowed_hosts_rejects_a_url_without_host(public_url: str) -> None:
    with pytest.raises(ValueError, match="GATEWAY_PUBLIC_URL"):
        allowed_hosts(public_url)


def test_public_host_is_rejected_when_not_configured() -> None:
    assert post_initialize(PUBLIC_URL, make_settings()).status_code == 421


@pytest.mark.parametrize(
    ("base_url", "status"),
    [(PUBLIC_URL, 200), (LOCAL_URL, 200), ("https://evil.example.com", 421)],
)
def test_host_allowlist_when_public_url_is_configured(base_url: str, status: int) -> None:
    settings = make_settings(gateway_public_url=PUBLIC_URL)

    assert post_initialize(base_url, settings).status_code == status


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 401),
        ({"Authorization": "Bearer wrong"}, 401),
        ({"Authorization": "s3cret"}, 401),
        ({"Authorization": "Basic s3cret"}, 401),
        # Caddy adds the token for Anthropic's range: an address never stands in for it (ADR-0019).
        ({"X-Forwarded-For": "160.79.104.7"}, 401),
        ({"Authorization": "Bearer s3cret"}, 200),
        ({"Authorization": "bearer s3cret"}, 200),
    ],
)
def test_bearer_token_is_enforced_when_configured(headers: dict[str, str], status: int) -> None:
    settings = make_settings(gateway_token="s3cret")

    assert post_initialize(LOCAL_URL, settings, headers).status_code == status


def test_bearer_token_also_guards_each_merchants_endpoint() -> None:
    settings = make_settings(gateway_token="s3cret")
    with TestClient(create_app(settings), base_url=LOCAL_URL) as client:
        response = client.post("/m/le-petit-zinc/mcp", json=INITIALIZE, headers=MCP_HEADERS)

    assert response.status_code == 401


@pytest.mark.parametrize(
    "slug",
    [
        "Not_A_Slug",
        # `core.models.Slug`, not a regex whose `$` also matches before a final newline (#220).
        "le-petit-zinc%0A",
    ],
    ids=["not-a-slug", "trailing-newline"],
)
def test_a_malformed_merchant_endpoint_is_not_found_without_the_database(slug: str) -> None:
    settings = make_settings(database_url="postgresql+psycopg://u:p@127.0.0.1:1/db")
    with TestClient(create_app(settings), base_url=LOCAL_URL) as client:
        response = client.post(f"/m/{slug}/mcp", json=INITIALIZE, headers=MCP_HEADERS)

    assert response.status_code == 404


def test_bearer_token_accepts_non_ascii() -> None:
    settings = make_settings(gateway_token="pässword")
    headers = {"Authorization": "Bearer pässword".encode()}

    assert post_initialize(LOCAL_URL, settings, headers).status_code == 200


async def test_the_app_releases_what_it_created_even_when_serving_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    released: list[str] = []

    class Engine:
        async def dispose(self) -> None:
            released.append("engine")

    class Agent:
        async def close(self) -> None:
            released.append("agent")

    monkeypatch.setattr("gateway.server.make_engine", lambda url: Engine())
    monkeypatch.setattr("gateway.server.make_agent_client", lambda settings, db: Agent())
    app = create_app(make_settings())

    # The MCP session manager's task group hands the failure on in an exception group.
    with pytest.raises(ExceptionGroup) as raised:
        async with app.router.lifespan_context(app):
            raise RuntimeError("serving failed")

    assert raised.group_contains(RuntimeError, match="serving failed")
    assert released == ["agent", "engine"]
