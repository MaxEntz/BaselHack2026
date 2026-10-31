from typing import Any

import httpx
import pytest
from mcp import Client
from starlette.testclient import TestClient

from core.config import Settings
from gateway.server import allowed_hosts, create_app, mcp

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


async def test_ping_returns_pong() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("ping", {})

    assert result.structured_content is not None
    assert result.structured_content["pong"] is True
    assert result.structured_content["server_time"]


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
        ({"Authorization": "Bearer s3cret"}, 200),
        ({"Authorization": "bearer s3cret"}, 200),
    ],
)
def test_bearer_token_is_enforced_when_configured(headers: dict[str, str], status: int) -> None:
    settings = make_settings(gateway_token="s3cret")

    assert post_initialize(LOCAL_URL, settings, headers).status_code == status


def test_bearer_token_accepts_non_ascii() -> None:
    settings = make_settings(gateway_token="pässword")
    headers = {"Authorization": "Bearer pässword".encode()}

    assert post_initialize(LOCAL_URL, settings, headers).status_code == 200
