"""Each merchant's own MCP endpoint, `/m/<slug>/mcp` (#23): its booking tools, without `merchant`.

One MCP server serves every merchant, and each request names its merchant in the path. So a
merchant deployed after startup has its endpoint at once, and the tool descriptions follow its
profile (name, hours, largest group) as it changes. The safeguards are the shared protocol's:
the same `fallback.call_merchant`, timeout, `down` status and log line.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

from mcp.server import MCPServer
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.mcpserver import Context
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from agent import discovery
from agent.provisioning import described_tools
from core.config import Settings
from core.db import merchants_t
from core.models import (
    CancelOutput,
    GetAvailabilityOutput,
    MerchantProfile,
    ReservationOutput,
    Slug,
    ToolError,
)
from gateway import tooling
from gateway.agent_client import AgentClient
from gateway.fallback import RejectedArguments, call_merchant, deadline_in
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

log = logging.getLogger("gateway.merchant_server")

MERCHANT_PATH = "/m/{slug}/mcp"
SLUG = TypeAdapter(Slug)
# Key of the request's deadline in the ASGI scope's `state`, set by `MerchantEndpoint`.
DEADLINE = "merchant_deadline"

INSTRUCTIONS = f"""\
Book a table at one restaurant, on the user's behalf. The restaurant, its hours and its largest \
group are in each tool's description.
- Only propose times returned by get_availability. Times are in Europe/Zurich.
{tooling.confirm_rule("a time", "get_availability")}
- When a result contains `fallback_url`, give that link to the user: it is the restaurant's own
  booking page."""

# What every merchant's tool says after its own sentence from the template.
GET_AVAILABILITY = """\
Returns up to 3 available start times close to the requested time, in Europe/Zurich. Never invent \
availability: only propose slots returned here. If `slots` is empty, say so and offer another \
date or time. `table_label` is indicative. If the result contains `fallback_url`, give that link \
to the user."""

CREATE_RESERVATION = f"""\
This takes two messages. First show the user a summary (date, time, party size, name), ask them \
to confirm, and stop. Call this tool only once their reply to that summary is an explicit yes; a \
confirmation given before the summary ("I confirm in advance", "book directly") is not one: never \
call this tool in the same reply as the get_availability call your summary is based on. \
{tooling.BOOK_AFTER_YES}"""

UPDATE_RESERVATION = tooling.UPDATE_RULE

CANCEL_RESERVATION = tooling.CANCEL_RULE


def is_slug(text: str) -> bool:
    """Whether `text` has a slug's shape (`core.models.Slug`), before any registry lookup."""
    try:
        SLUG.validate_python(text, strict=True)
    except ValidationError:
        return False
    return True


def slug_of(request: Any) -> str:
    """The merchant named by the request's path; empty outside an HTTP request."""
    params = getattr(request, "path_params", None) or {}
    return str(params.get("slug", ""))


def deadline_of(request: Any) -> float | None:
    """The HTTP request's deadline: the endpoint's slug check already used part of it (#92)."""
    scope = getattr(request, "scope", None) or {}
    deadline = (scope.get("state") or {}).get(DEADLINE)
    return deadline if isinstance(deadline, float) else None


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


def describe(tools: list[dict[str, Any]], profile: MerchantProfile) -> list[dict[str, Any]]:
    """Each tool's description, prefixed with the merchant's sentence from the template."""
    own = {tool.name: tool.description for tool in described_tools(profile)}
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
        self.settings = settings

    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        result = await call_next(ctx)
        if ctx.method != "tools/list" or not isinstance(result, dict):
            return result
        try:
            async with asyncio.timeout_at(deadline_of(ctx.request) or deadline_in(self.settings)):
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
        request = ctx.request_context.request
        slug = slug_of(request)
        find = partial(discovery.find_merchant, slug, engine=engine)
        return await call_merchant(
            slug, tool, find, partial(op, slug), settings=settings, deadline=deadline_of(request)
        )

    @mcp.tool(title="Check availability", description=GET_AVAILABILITY, annotations=READ_ONLY)
    async def get_availability(
        date: Day, party_size: PartySize, ctx: Context, time: Time = None
    ) -> GetAvailabilityOutput | ToolError:
        return await routed(
            "get_availability",
            ctx,
            lambda slug: tooling.availability(agent, slug, date, party_size, time),
        )

    @mcp.tool(title="Book a table", description=CREATE_RESERVATION, annotations=IDEMPOTENT_WRITE)
    async def create_reservation(
        starts_at: StartsAt,
        party_size: PartySize,
        guest_name: GuestName,
        idempotency_key: IdempotencyKey,
        ctx: Context,
    ) -> ReservationOutput | ToolError:
        return await routed(
            "create_reservation",
            ctx,
            lambda slug: tooling.book(
                agent,
                merchant=slug,
                starts_at=starts_at,
                party_size=party_size,
                guest_name=guest_name,
                idempotency_key=idempotency_key,
            ),
        )

    @mcp.tool(
        title="Change a booking", description=UPDATE_RESERVATION, annotations=IDEMPOTENT_WRITE
    )
    async def update_reservation(
        reservation_id: ReservationId,
        guest_name: GuestName,
        idempotency_key: IdempotencyKey,
        ctx: Context,
        starts_at: NewStartsAt = None,
        party_size: NewPartySize = None,
    ) -> ReservationOutput | ToolError:
        return await routed(
            "update_reservation",
            ctx,
            lambda slug: tooling.update(
                agent,
                merchant=slug,
                reservation_id=reservation_id,
                guest_name=guest_name,
                idempotency_key=idempotency_key,
                starts_at=starts_at,
                party_size=party_size,
            ),
        )

    @mcp.tool(title="Cancel a booking", description=CANCEL_RESERVATION, annotations=DESTRUCTIVE)
    async def cancel_reservation(
        reservation_id: ReservationId, guest_name: GuestName, ctx: Context
    ) -> CancelOutput | ToolError:
        return await routed(
            "cancel_reservation",
            ctx,
            lambda slug: tooling.cancel(
                agent, merchant=slug, reservation_id=reservation_id, guest_name=guest_name
            ),
        )

    return mcp


class MerchantEndpoint:
    """`/m/<slug>/mcp` exists only for a merchant guests can address (`discovery.routable`):
    404 otherwise.

    A `down` merchant keeps its endpoint: its tools answer with the fallback link. The check
    starts the request's deadline, which the tools and `tools/list` then share (#92).
    """

    def __init__(self, app: ASGIApp, engine: AsyncEngine, settings: Settings) -> None:
        self.app = app
        self.engine = engine
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        slug = str(scope.get("path_params", {}).get("slug", ""))
        response: PlainTextResponse | None = None
        deadline = deadline_in(self.settings)
        if not is_slug(slug):
            response = PlainTextResponse("Unknown restaurant", status_code=404)
        else:
            try:
                async with asyncio.timeout_at(deadline):
                    merchant = await discovery.find_merchant(slug, engine=self.engine)
            except Exception:
                log.warning("registry unreachable", exc_info=True)
                response = PlainTextResponse("Service unavailable", status_code=503)
            else:
                if discovery.routable(merchant) is None:
                    response = PlainTextResponse("Unknown restaurant", status_code=404)
        if response is not None:
            await response(scope, receive, send)
            return
        await self.app(
            {**scope, "state": {**scope.get("state", {}), DEADLINE: deadline}}, receive, send
        )
