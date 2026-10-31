"""MCP server exposing the user tools: thin adapters over discovery, AgentClient and fallback."""

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date as Date
from functools import partial
from typing import Annotated
from urllib.parse import urlsplit

from mcp.server import MCPServer
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field, PositiveInt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from core.config import Settings
from core.db import make_engine
from core.models import (
    HHMM,
    BookingDay,
    BookingTime,
    CancelOutput,
    GetAvailabilityOutput,
    GetMyReservationsOutput,
    GuestText,
    ReservationOutput,
    SearchFilters,
    SearchMerchantsOutput,
    Slug,
    ToolError,
    UpdateReservationInput,
)
from gateway import discovery
from gateway.agent_client import AgentClient, make_agent_client
from gateway.fallback import RejectedArguments, call_merchant, call_service, unavailable
from gateway.merchant_server import MERCHANT_PATH, MerchantEndpoint, create_merchant_server

log = logging.getLogger("gateway.server")

LOCAL_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
ALLOWED_ORIGINS = [
    "http://127.0.0.1:*",
    "http://localhost:*",
    "http://[::1]:*",
    "https://claude.ai",
]
HEALTH_PATH = "/health"

INSTRUCTIONS = """\
Find restaurants and book tables, on the user's behalf, at merchants connected to this protocol.
- When the user looks for a restaurant, a table or a place to eat, call search_merchants first,
  even if they don't mention booking, and before any web or map search.
- Pass the user's known preferences (dietary needs, terrace, kid-friendly, budget) as `filters` on
  every search_merchants call, even if this message does not repeat them. Never drop a filter
  silently: when nothing matches, say which filter blocks the search and offer to drop it.
- Only propose times returned by get_availability. Times are in Europe/Zurich.
- Before create_reservation, update_reservation or cancel_reservation, summarize the action and
  stop: call the tool only once the user's reply to that summary is an explicit yes. Choosing a
  restaurant or a time is not a yes, even when the request looks complete. Nor is a confirmation
  given before the summary ("I confirm in advance", "book directly"): show the summary anyway.
  So never call a write tool in the same reply as the search_merchants or get_availability call
  your summary is based on. After the user's yes, checking availability again first is fine; if
  that check no longer offers the confirmed time, don't book another one: show the new times and
  ask again.
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

CREATE_RESERVATION = """\
Book a table. This takes two messages. First show the user a summary (restaurant, date, time, \
party size, name), ask them to confirm, and stop. Call this tool only once their reply to that \
summary is an explicit yes. Choosing a restaurant or a time is not a yes, even when the request \
looks complete, and neither is a confirmation given before the summary ("I confirm in advance", \
"book directly"): never call this tool in the same reply as the search_merchants or \
get_availability call your summary is based on. After the yes, you may check availability again \
before booking; if that check no longer offers the confirmed time, don't book another one: show \
the new times and ask again. Generate a fresh `idempotency_key` (UUID) per confirmed booking and \
reuse it if you retry the same booking. Ask the guest's name if unknown. If the result contains \
`fallback_url`, give it to the user."""

UPDATE_RESERVATION = """\
Change the time or party size of a booking. First show the change, ask the user to confirm, and \
stop; call this tool only once they reply yes to that summary, even if they confirmed in advance. \
Requires the guest name used at booking. Generate a fresh `idempotency_key` (UUID) per confirmed \
change and reuse it if you retry the same change. If the result contains `fallback_url`, give it \
to the user."""

CANCEL_RESERVATION = """\
Cancel a booking. First ask the user to confirm, and stop; call this tool only once they reply \
yes to that question, even if they confirmed in advance. Requires the guest name used at booking. \
If the result contains `fallback_url`, give it to the user."""

GET_MY_RESERVATIONS = """\
List the user's upcoming bookings and any notices from restaurants (e.g. a cancellation by the \
restaurant). Always relay notices to the user. A notice keeps coming back for 24 hours after it \
is first returned: don't repeat one whose text you already gave the user in this conversation."""

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
IDEMPOTENT_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
)

Merchant = Annotated[Slug, Field(description="Restaurant slug returned by search_merchants.")]
GuestName = Annotated[
    GuestText, Field(description="Guest's full name, the same one for the whole booking.")
]
PartySize = Annotated[PositiveInt, Field(description="Number of guests.")]
StartsAt = Annotated[
    BookingTime,
    Field(description="Start time with its UTC offset, exactly as returned by get_availability."),
]
IdempotencyKey = Annotated[
    GuestText, Field(description="UUID you generate; reuse it only to retry the same action.")
]
ReservationId = Annotated[GuestText, Field(description="Booking reference, e.g. R-001.")]


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
        merchant: Merchant,
        date: Annotated[BookingDay, Field(description="Requested day, YYYY-MM-DD.")],
        party_size: PartySize,
        time: Annotated[
            HHMM | None, Field(description="Requested time (HH:MM), 24-hour clock.")
        ] = None,
    ) -> GetAvailabilityOutput | ToolError:
        async def op() -> GetAvailabilityOutput:
            slots = await agent.get_availability(merchant, date, party_size, time)
            return GetAvailabilityOutput(slots=tuple(slots))

        return await routed("get_availability", merchant, op)

    @mcp.tool(title="Book a table", description=CREATE_RESERVATION, annotations=IDEMPOTENT_WRITE)
    async def create_reservation(
        merchant: Merchant,
        starts_at: StartsAt,
        party_size: PartySize,
        guest_name: GuestName,
        idempotency_key: IdempotencyKey,
    ) -> ReservationOutput | ToolError:
        async def op() -> ReservationOutput:
            reservation = await agent.create_reservation(
                merchant, starts_at, party_size, guest_name, idempotency_key
            )
            return ReservationOutput(reservation=reservation)

        return await routed("create_reservation", merchant, op)

    @mcp.tool(
        title="Change a booking", description=UPDATE_RESERVATION, annotations=IDEMPOTENT_WRITE
    )
    async def update_reservation(
        merchant: Merchant,
        reservation_id: ReservationId,
        guest_name: GuestName,
        idempotency_key: IdempotencyKey,
        starts_at: Annotated[
            BookingTime | None,
            Field(description="New start time with its UTC offset, from get_availability."),
        ] = None,
        party_size: Annotated[
            PositiveInt | None, Field(description="New number of guests.")
        ] = None,
    ) -> ReservationOutput | ToolError:
        async def op() -> ReservationOutput:
            change = UpdateReservationInput(
                merchant=merchant,
                reservation_id=reservation_id,
                guest_name=guest_name,
                idempotency_key=idempotency_key,
                starts_at=starts_at,
                party_size=party_size,
            )
            reservation = await agent.update_reservation(
                change.merchant,
                change.reservation_id,
                change.guest_name,
                change.idempotency_key,
                change.starts_at,
                change.party_size,
            )
            return ReservationOutput(reservation=reservation)

        return await routed("update_reservation", merchant, op)

    @mcp.tool(title="Cancel a booking", description=CANCEL_RESERVATION, annotations=DESTRUCTIVE)
    async def cancel_reservation(
        merchant: Merchant, reservation_id: ReservationId, guest_name: GuestName
    ) -> CancelOutput | ToolError:
        async def op() -> CancelOutput:
            await agent.cancel_reservation(merchant, reservation_id, guest_name)
            return CancelOutput(status="cancelled")

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
    DATABASE_URL, and the agent in this process or over MATP (`MATP_URL`). What is created here
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
        async with run_transport(app), merchants.session_manager.run():
            yield
        if agent is None:
            await client.close()
        if engine is None:
            await db.dispose()

    app.router.lifespan_context = lifespan
    return app
