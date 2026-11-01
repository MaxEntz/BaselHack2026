"""MCP server exposing the user tools: thin adapters over discovery, AgentClient and fallback."""

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import date as Date
from functools import partial
from typing import Annotated
from urllib.parse import urlsplit

from mcp.server import MCPServer
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from agent import discovery
from core.config import Settings
from core.db import make_engine
from core.models import (
    CancelOutput,
    GetAvailabilityOutput,
    GetMyReservationsOutput,
    ReservationOutput,
    SearchFilters,
    SearchMerchantsOutput,
    Slug,
    ToolError,
)
from gateway import tooling
from gateway.agent_client import AgentClient, make_agent_client
from gateway.fallback import RejectedArguments, call_merchant, call_service, unavailable
from gateway.merchant_server import MERCHANT_PATH, MerchantEndpoint, create_merchant_server
from gateway.tooling import (
    DESTRUCTIVE,
    IDEMPOTENT_WRITE,
    READ_ONLY,
    Day,
    GuestName,
    IdempotencyKey,
    NewPartySize,
    NewStartsAt,
    PartySize,
    ReservationId,
    StartsAt,
    Time,
)

log = logging.getLogger("gateway.server")

LOCAL_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
ALLOWED_ORIGINS = [
    "http://127.0.0.1:*",
    "http://localhost:*",
    "http://[::1]:*",
    "https://claude.ai",
]
HEALTH_PATH = "/health"

INSTRUCTIONS = f"""\
Find restaurants and book tables, on the user's behalf, at merchants connected to this protocol.
- When the user looks for a restaurant, a table or a place to eat, call search_merchants first,
  even if they don't mention booking, and before any web or map search.
- Pass the user's known preferences (dietary needs, terrace, kid-friendly, budget) as `filters` on
  every search_merchants call, even if this message does not repeat them. Never drop a filter
  silently: when nothing matches, say which filter blocks the search and offer to drop it.
- Only propose times returned by get_availability. Times are in Europe/Zurich.
{tooling.confirm_rule("a restaurant or a time", "search_merchants or get_availability")}
- When a result contains `fallback_url`, give that link to the user: it is the restaurant's own
  booking page.
- Relay the notices returned by get_my_reservations. A notice can come back for 24 hours: tell
  the user each one once per conversation, matching notices by their text."""

SEARCH_MERCHANTS = """\
Find restaurants that can be booked through this connector. Use this tool first whenever the \
user looks for a restaurant, a table or a place to eat, even if they don't mention booking, and \
before any web or map search. ALWAYS pass the user's known \
preferences (dietary needs such as vegetarian/vegan/gluten-free/halal/kosher, terrace, \
kid-friendly, budget) as `filters`, even if the user did not repeat them in this message. Budget \
maps to `max_price` (€ cheap … €€€€ fine dining). If `merchants` is empty and `suggestions` is \
not, tell the user which filter blocks the search and offer to drop it; never drop a filter \
silently. `suggestions[].drop` names the filter and `matches` how many restaurants dropping it \
would find. If `ignored_filters` is not empty, tell the user those preferences could not be \
applied. If the result contains `fallback_url`, the restaurant is not bookable here right now: \
give that link to the user."""

GET_AVAILABILITY = """\
Get up to 3 available start times close to the requested time. Times are in Europe/Zurich. Never \
invent availability: only propose slots returned here. If `slots` is empty, say so and offer \
another date or time. `table_label` is indicative: don't promise a table, it is assigned at \
booking. If the result contains `fallback_url`, give that link to the user."""

CREATE_RESERVATION = f"""\
Book a table. This takes two messages. First show the user a summary (restaurant, date, time, \
party size, name), ask them to confirm, and stop. Call this tool only once their reply to that \
summary is an explicit yes. Choosing a restaurant or a time is not a yes, even when the request \
looks complete, and neither is a confirmation given before the summary ("I confirm in advance", \
"book directly"): never call this tool in the same reply as the search_merchants or \
get_availability call your summary is based on. {tooling.BOOK_AFTER_YES}"""

UPDATE_RESERVATION = f"Change the time or party size of a booking. {tooling.UPDATE_RULE}"

CANCEL_RESERVATION = f"Cancel a booking. {tooling.CANCEL_RULE}"

GET_MY_RESERVATIONS = """\
List the user's upcoming bookings and any notices from restaurants (e.g. a cancellation by the \
restaurant). Always relay notices to the user. A notice keeps coming back for 24 hours after it \
is first returned: don't repeat one whose text you already gave the user in this conversation."""

Merchant = Annotated[Slug, Field(description="Restaurant slug returned by search_merchants.")]


def create_server(agent: AgentClient, engine: AsyncEngine, settings: Settings) -> MCPServer:
    """The 6 user tools. Each one answers with its result or a `ToolError`, and never raises."""
    mcp = MCPServer("merchant-agents", instructions=INSTRUCTIONS, middleware=[RejectedArguments()])

    async def routed[T](tool: str, slug: str, op: Callable[[], Awaitable[T]]) -> T | ToolError:
        find = partial(discovery.find_merchant, slug, engine=engine)
        return await call_merchant(slug, tool, find, op, settings=settings)

    @mcp.tool(title="Search restaurants", description=SEARCH_MERCHANTS, annotations=READ_ONLY)
    async def search_merchants(
        query: Annotated[
            str,
            Field(
                max_length=discovery.MAX_QUERY,
                description="What the user asked for, in their own words.",
            ),
        ],
        near: Annotated[str | None, Field(description="City, if the user named one.")] = None,
        date: Annotated[
            Date | None, Field(description="Requested day. Not used to filter yet.")
        ] = None,
        filters: Annotated[
            SearchFilters | None,
            Field(
                description=(
                    "The user's preferences: `tags` (vegetarian, vegan, gluten-free, halal, "
                    "kosher, terrace, kid-friendly) and `max_price`."
                )
            ),
        ] = None,
    ) -> SearchMerchantsOutput | ToolError:
        async def op() -> SearchMerchantsOutput | ToolError:
            discovery.check_search_bounds(query, filters)
            if down := await discovery.find_named_down_merchant(query, engine=engine):
                return unavailable(down)
            return await agent.search_merchants(query, near, date, filters)

        named = partial(discovery.find_named_merchant, query, engine=engine)
        return await call_service("search_merchants", op, settings=settings, named=named)

    @mcp.tool(title="Check availability", description=GET_AVAILABILITY, annotations=READ_ONLY)
    async def get_availability(
        merchant: Merchant, date: Day, party_size: PartySize, time: Time = None
    ) -> GetAvailabilityOutput | ToolError:
        op = partial(tooling.availability, agent, merchant, date, party_size, time)
        return await routed("get_availability", merchant, op)

    @mcp.tool(title="Book a table", description=CREATE_RESERVATION, annotations=IDEMPOTENT_WRITE)
    async def create_reservation(
        merchant: Merchant,
        starts_at: StartsAt,
        party_size: PartySize,
        guest_name: GuestName,
        idempotency_key: IdempotencyKey,
    ) -> ReservationOutput | ToolError:
        op = partial(
            tooling.book,
            agent,
            merchant=merchant,
            starts_at=starts_at,
            party_size=party_size,
            guest_name=guest_name,
            idempotency_key=idempotency_key,
        )
        return await routed("create_reservation", merchant, op)

    @mcp.tool(
        title="Change a booking", description=UPDATE_RESERVATION, annotations=IDEMPOTENT_WRITE
    )
    async def update_reservation(
        merchant: Merchant,
        reservation_id: ReservationId,
        guest_name: GuestName,
        idempotency_key: IdempotencyKey,
        starts_at: NewStartsAt = None,
        party_size: NewPartySize = None,
    ) -> ReservationOutput | ToolError:
        op = partial(
            tooling.update,
            agent,
            merchant=merchant,
            reservation_id=reservation_id,
            guest_name=guest_name,
            idempotency_key=idempotency_key,
            starts_at=starts_at,
            party_size=party_size,
        )
        return await routed("update_reservation", merchant, op)

    @mcp.tool(title="Cancel a booking", description=CANCEL_RESERVATION, annotations=DESTRUCTIVE)
    async def cancel_reservation(
        merchant: Merchant, reservation_id: ReservationId, guest_name: GuestName
    ) -> CancelOutput | ToolError:
        op = partial(
            tooling.cancel,
            agent,
            merchant=merchant,
            reservation_id=reservation_id,
            guest_name=guest_name,
        )
        return await routed("cancel_reservation", merchant, op)

    @mcp.tool(title="My bookings", description=GET_MY_RESERVATIONS, annotations=READ_ONLY)
    async def get_my_reservations(guest_name: GuestName) -> GetMyReservationsOutput | ToolError:
        async def op() -> GetMyReservationsOutput:
            reservations, notices = await agent.get_my_reservations(guest_name)
            return GetMyReservationsOutput(reservations=tuple(reservations), notices=tuple(notices))

        return await call_service("get_my_reservations", op, settings=settings)

    @mcp.custom_route(HEALTH_PATH, methods=["GET"])  # type: ignore[untyped-decorator]
    async def health(_: Request) -> JSONResponse:
        """Demo monitoring: is the gateway up, and can it reach Postgres?"""
        try:
            async with asyncio.timeout(settings.agent_timeout_ms / 1000), engine.connect() as conn:
                await conn.execute(select(1))
        except Exception:
            log.warning("database unreachable", exc_info=True)
            return JSONResponse({"status": "degraded", "database": "unreachable"}, 503)
        return JSONResponse({"status": "ok", "database": "ok"})

    return mcp


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
    """Reject HTTP requests that don't carry `Authorization: Bearer <token>`, except `/health`.

    Plain ASGI rather than BaseHTTPMiddleware, which buffers Streamable HTTP's SSE responses.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.expected = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] != HEALTH_PATH:
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


def create_app(
    settings: Settings, *, agent: AgentClient | None = None, engine: AsyncEngine | None = None
) -> Starlette:
    """Build the ASGI app: Host allowlist from the public URL, bearer token when one is set.

    `agent` and `engine` default to the real ones (tests inject theirs): the engine on
    DATABASE_URL, and the agent in this process or over BATP (`BATP_URL`). What is created here
    is released at shutdown.
    """
    db = engine or make_engine(settings.database_url)
    client = agent or make_agent_client(settings, db)
    security = TransportSecuritySettings(
        allowed_hosts=allowed_hosts(settings.gateway_public_url),
        allowed_origins=ALLOWED_ORIGINS,
    )
    app = create_server(client, db, settings).streamable_http_app(transport_security=security)
    run_transport = app.router.lifespan_context
    # Each merchant's own endpoint (#23): a second MCP server, routed by the slug in the path.
    merchants = create_merchant_server(client, db, settings)
    merchants.streamable_http_app(transport_security=security)  # creates its session manager
    endpoint = StreamableHTTPASGIApp(merchants.session_manager)
    app.router.routes.append(Route(MERCHANT_PATH, MerchantEndpoint(endpoint, db, settings)))
    if settings.gateway_token:
        app.add_middleware(BearerAuth, token=settings.gateway_token.get_secret_value())

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        # Unwound in reverse even when the transport fails, so what was created here is released.
        async with AsyncExitStack() as owned:
            if engine is None:
                owned.push_async_callback(db.dispose)
            if agent is None:
                owned.push_async_callback(client.close)
            await owned.enter_async_context(run_transport(app))
            await owned.enter_async_context(merchants.session_manager.run())
            yield

    app.router.lifespan_context = lifespan
    return app
