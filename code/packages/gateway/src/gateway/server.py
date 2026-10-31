"""MCP server exposing the user tools (search, availability, reservations)."""

import secrets
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from core.config import Settings

LOCAL_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
ALLOWED_ORIGINS = [
    "http://127.0.0.1:*",
    "http://localhost:*",
    "http://[::1]:*",
    "https://claude.ai",
]

mcp = MCPServer("merchant-agents")


@mcp.tool()
def ping() -> dict[str, Any]:
    """Health check. Returns pong and the server time."""
    return {"pong": True, "server_time": datetime.now(UTC).isoformat()}


def allowed_hosts(public_url: str | None) -> list[str]:
    """Hosts the transport accepts: localhost, plus the public domain when there is one."""
    if not public_url:
        return LOCAL_HOSTS
    url = public_url if "://" in public_url else f"https://{public_url}"
    host = urlsplit(url).hostname
    if not host:
        raise ValueError(f"GATEWAY_PUBLIC_URL has no host: {public_url!r}")
    return [*LOCAL_HOSTS, host, f"{host}:*"]


class BearerAuth:
    """Reject HTTP requests that don't carry `Authorization: Bearer <token>`.

    Plain ASGI rather than BaseHTTPMiddleware, which buffers Streamable HTTP's SSE responses.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.expected = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            header = Headers(scope=scope).get("authorization", "")
            scheme, _, credentials = header.partition(" ")
            # Headers decodes as latin-1: encoding it back recovers the bytes on the wire.
            valid = secrets.compare_digest(credentials.encode("latin-1"), self.expected)
            if scheme.lower() != "bearer" or not valid:
                response = PlainTextResponse(
                    "Unauthorized", status_code=401, headers={"WWW-Authenticate": "Bearer"}
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(settings: Settings) -> Starlette:
    """Build the ASGI app: Host allowlist from the public URL, bearer token when one is set."""
    app = mcp.streamable_http_app(
        transport_security=TransportSecuritySettings(
            allowed_hosts=allowed_hosts(settings.gateway_public_url),
            allowed_origins=ALLOWED_ORIGINS,
        )
    )
    if settings.gateway_token:
        app.add_middleware(BearerAuth, token=settings.gateway_token.get_secret_value())
    return app
