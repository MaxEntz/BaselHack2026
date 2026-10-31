"""Each merchant's own MCP endpoint, `/m/<slug>/mcp` (#23): its booking tools, without `merchant`.

One MCP server serves every merchant, and each request names its merchant in the path. So a
merchant deployed after startup has its endpoint at once, and the tool descriptions follow its
profile (name, hours, largest group) as it changes. The safeguards are the shared protocol's:
the same `fallback.call_merchant`, timeout, `down` status and log line.
"""

import asyncio
import logging
import re
import unicodedata
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import Field, PositiveInt, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from agent.provisioning import generated_tools
from core.config import Settings
from core.db import merchants_t
from core.models import (
    HHMM,
    BookingDay,
    BookingTime,
    CancelOutput,
    GetAvailabilityOutput,
    MerchantProfile,
    NonEmptyStr,
    ReservationOutput,
    ToolError,
    UpdateReservationInput,
)
from gateway import discovery
from gateway.agent_client import AgentClient
from gateway.fallback import RejectedArguments, call_merchant

log = logging.getLogger("gateway.merchant_server")

MERCHANT_PATH = "/m/{slug}/mcp"
SLUG = re.compile(r"^[a-z0-9-]{3,61}$")
# Merchant free text allowed in a tool description (name, city).
MAX_FREE_TEXT = 80
# Instead of the merchant's own words, which could carry instructions for Claude.
POLICY_ELSEWHERE = "see the restaurant's own website"

INSTRUCTIONS = """\
Book a table at one restaurant, on the user's behalf. The restaurant, its hours and its largest \
group are in each tool's description.
- Only propose times returned by get_availability. Times are in Europe/Zurich.
- Before create_reservation, update_reservation or cancel_reservation, summarize the action and
  stop: call the tool only once the user's reply to that summary is an explicit yes. Choosing a
  time is not a yes, even when the request looks complete.
- When a result contains `fallback_url`, give that link to the user: it is the restaurant's own
  booking page."""

# What every merchant's tool says after its own sentence from the template.
GET_AVAILABILITY = """\
Returns up to 3 available start times close to the requested time, in Europe/Zurich. Never invent \
availability: only propose slots returned here. If `slots` is empty, say so and offer another \
date or time. `table_label` is indicative. If the result contains `fallback_url`, give that link \
to the user."""

CREATE_RESERVATION = """\
This takes two messages. First show the user a summary (date, time, party size, name), ask them \
to confirm, and stop. Call this tool only once their reply to that summary is an explicit yes. \
Generate a fresh `idempotency_key` (UUID) per confirmed booking and reuse it if you retry the \
same booking. Ask the guest's name if unknown. If the result contains `fallback_url`, give it to \
the user."""

UPDATE_RESERVATION = """\
First show the change, ask the user to confirm, and stop; call this tool only once they reply \
yes. Requires the guest name used at booking. Generate a fresh `idempotency_key` (UUID) per \
confirmed change and reuse it if you retry the same change. If the result contains \
`fallback_url`, give it to the user."""

CANCEL_RESERVATION = """\
First ask the user to confirm, and stop; call this tool only once they reply yes. Requires the \
guest name used at booking. If the result contains `fallback_url`, give it to the user."""

# Same hints as the shared protocol's tools (gateway.server).
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
IDEMPOTENT_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
)

GuestName = Annotated[
    NonEmptyStr, Field(description="Guest's full name, the same one for the whole booking.")
]
PartySize = Annotated[PositiveInt, Field(description="Number of guests.")]
StartsAt = Annotated[
    BookingTime,
    Field(description="Start time with its UTC offset, exactly as returned by get_availability."),
]
IdempotencyKey = Annotated[
    NonEmptyStr, Field(description="UUID you generate; reuse it only to retry the same action.")
]
ReservationId = Annotated[NonEmptyStr, Field(description="Booking reference, e.g. R-001.")]


def slug_of(request: Any) -> str:
    """The merchant named by the request's path; empty outside an HTTP request."""
    params = getattr(request, "path_params", None) or {}
    return str(params.get("slug", ""))


async def load_profile(slug: str, *, engine: AsyncEngine) -> MerchantProfile | None:
    """The profile behind the merchant's descriptions: the registry's copy, kept in sync (#9)."""
    query = select(merchants_t.c.profile).where(merchants_t.c.slug == slug)
    async with engine.connect() as conn:
        profile = (await conn.execute(query)).scalar_one_or_none()
    if profile is None:
        return None
    try:
        return MerchantProfile.model_validate(profile)
    except ValidationError as error:
        log.warning(
            "registry profile skipped",
            extra={"slug": slug, "errors": error.errors(include_input=False)},
        )
        return None


def one_line(text: str, limit: int = MAX_FREE_TEXT) -> str:
    """Merchant text fit for a tool description: one line, no control characters, bounded."""
    printable = "".join(" " if unicodedata.category(char)[0] == "C" else char for char in text)
    line = " ".join(printable.split())
    return line if len(line) <= limit else f"{line[: limit - 1].rstrip()}…"


def described_profile(profile: MerchantProfile) -> MerchantProfile:
    """The profile as the descriptions may show it.

    Tool descriptions read as the connector's instructions, and the free text of a profile comes
    from the merchant's website, which anyone can onboard (#22): the cancellation policy never
    goes in, and the name and city are bounded. Services and party size are validated already.
    """
    location = profile.location
    if location and location.city:
        location = location.model_copy(update={"city": one_line(location.city)})
    return profile.model_copy(
        update={
            "name": one_line(profile.name),
            "location": location,
            "cancellation_policy": POLICY_ELSEWHERE,
        }
    )


def describe(tools: list[dict[str, Any]], profile: MerchantProfile) -> list[dict[str, Any]]:
    """Each tool's description, prefixed with the merchant's sentence from the template."""
    own = {tool.name: tool.description for tool in generated_tools(described_profile(profile))}
    return [
        {**tool, "description": f"{own[tool['name']]} {tool.get('description') or ''}".strip()}
        if tool.get("name") in own
        else tool
        for tool in tools
    ]


class MerchantDescriptions:
    """`tools/list` answers with the requested merchant's descriptions.

    Read from the registry at each listing, so a policy change shows at the next one. Without the
    profile (registry unreachable, row outside the contract), the generic descriptions stay.
    """

    def __init__(self, engine: AsyncEngine, settings: Settings) -> None:
        self.engine = engine
        self.timeout = settings.agent_timeout_ms / 1000

    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        result = await call_next(ctx)
        if ctx.method != "tools/list" or not isinstance(result, dict):
            return result
        try:
            async with asyncio.timeout(self.timeout):
                profile = await load_profile(slug_of(ctx.request), engine=self.engine)
        except Exception:
            log.warning("merchant profile unavailable", exc_info=True)
            return result
        if profile is None:
            return result
        return {**result, "tools": describe(result.get("tools", []), profile)}


def create_merchant_server(
    agent: AgentClient, engine: AsyncEngine, settings: Settings
) -> MCPServer:
    """The 4 booking tools of the merchant named by the path. They never raise."""
    mcp = MCPServer(
        "merchant-agents-restaurant",
        instructions=INSTRUCTIONS,
        middleware=[RejectedArguments(), MerchantDescriptions(engine, settings)],
    )

    async def routed[T](
        tool: str, ctx: Context, op: Callable[[str], Awaitable[T]]
    ) -> T | ToolError:
        slug = slug_of(ctx.request_context.request)
        find = partial(discovery.find_merchant, slug, engine=engine)
        return await call_merchant(slug, tool, find, partial(op, slug), settings=settings)

    @mcp.tool(title="Check availability", description=GET_AVAILABILITY, annotations=READ_ONLY)
    async def get_availability(
        date: Annotated[BookingDay, Field(description="Requested day, YYYY-MM-DD.")],
        party_size: PartySize,
        ctx: Context,
        time: Annotated[
            HHMM | None, Field(description="Requested time (HH:MM), 24-hour clock.")
        ] = None,
    ) -> GetAvailabilityOutput | ToolError:
        async def op(slug: str) -> GetAvailabilityOutput:
            slots = await agent.get_availability(slug, date, party_size, time)
            return GetAvailabilityOutput(slots=tuple(slots))

        return await routed("get_availability", ctx, op)

    @mcp.tool(title="Book a table", description=CREATE_RESERVATION, annotations=IDEMPOTENT_WRITE)
    async def create_reservation(
        starts_at: StartsAt,
        party_size: PartySize,
        guest_name: GuestName,
        idempotency_key: IdempotencyKey,
        ctx: Context,
    ) -> ReservationOutput | ToolError:
        async def op(slug: str) -> ReservationOutput:
            reservation = await agent.create_reservation(
                slug, starts_at, party_size, guest_name, idempotency_key
            )
            return ReservationOutput(reservation=reservation)

        return await routed("create_reservation", ctx, op)

    @mcp.tool(
        title="Change a booking", description=UPDATE_RESERVATION, annotations=IDEMPOTENT_WRITE
    )
    async def update_reservation(
        reservation_id: ReservationId,
        guest_name: GuestName,
        idempotency_key: IdempotencyKey,
        ctx: Context,
        starts_at: Annotated[
            BookingTime | None,
            Field(description="New start time with its UTC offset, from get_availability."),
        ] = None,
        party_size: Annotated[
            PositiveInt | None, Field(description="New number of guests.")
        ] = None,
    ) -> ReservationOutput | ToolError:
        async def op(slug: str) -> ReservationOutput:
            change = UpdateReservationInput(
                merchant=slug,
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

        return await routed("update_reservation", ctx, op)

    @mcp.tool(title="Cancel a booking", description=CANCEL_RESERVATION, annotations=DESTRUCTIVE)
    async def cancel_reservation(
        reservation_id: ReservationId, guest_name: GuestName, ctx: Context
    ) -> CancelOutput | ToolError:
        async def op(slug: str) -> CancelOutput:
            await agent.cancel_reservation(slug, reservation_id, guest_name)
            return CancelOutput(status="cancelled")

        return await routed("cancel_reservation", ctx, op)

    return mcp


class MerchantEndpoint:
    """`/m/<slug>/mcp` exists only for a known merchant that is not `building`: 404 otherwise.

    A `down` merchant keeps its endpoint: its tools answer with the fallback link.
    """

    def __init__(self, app: ASGIApp, engine: AsyncEngine, settings: Settings) -> None:
        self.app = app
        self.engine = engine
        self.timeout = settings.agent_timeout_ms / 1000

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        slug = str(scope.get("path_params", {}).get("slug", ""))
        response: PlainTextResponse | None = None
        if not SLUG.match(slug):
            response = PlainTextResponse("Unknown restaurant", status_code=404)
        else:
            try:
                async with asyncio.timeout(self.timeout):
                    merchant = await discovery.find_merchant(slug, engine=self.engine)
            except Exception:
                log.warning("registry unreachable", exc_info=True)
                response = PlainTextResponse("Service unavailable", status_code=503)
            else:
                if merchant is None or merchant.status == "building":
                    response = PlainTextResponse("Unknown restaurant", status_code=404)
        if response is not None:
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
