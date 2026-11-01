"""Each merchant's own MCP endpoint, `/m/<slug>/mcp` (#23): its booking tools, without `merchant`.

One MCP server serves every merchant, and each request names its merchant in the path. So a
merchant deployed after startup has its endpoint at once, and the tool descriptions follow its
profile (name, hours, largest group) as it changes. The safeguards are the shared protocol's:
the same `fallback.call_merchant`, timeout, `down` status and log line.
"""

import asyncio
import logging
from typing import Any

from mcp.server import MCPServer
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.mcpserver import Context
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from agent import discovery
from agent.provisioning import described_profile, described_tools
from core.config import Settings
from core.models import (
    MAX_SLOTS,
    CancelOutput,
    GetAvailabilityOutput,
    MerchantProfile,
    ReservationOutput,
    Slug,
    TalkOutput,
    ToolError,
)
from gateway import tooling
from gateway.agent_client import AgentClient
from gateway.fallback import TALK_TOOL, RejectedArguments, deadline_in
from gateway.tooling import (
    CONVERSATION,
    DESTRUCTIVE,
    IDEMPOTENT_WRITE,
    READ_ONLY,
    Confirm,
    Conversation,
    Day,
    GuestName,
    IdempotencyKey,
    Message,
    NewPartySize,
    NewStartsAt,
    PartySize,
    ReservationId,
    StartsAt,
    TalkKey,
    Time,
)

log = logging.getLogger("gateway.merchant_server")

MERCHANT_PATH = "/m/{slug}/mcp"
SLUG = TypeAdapter(Slug)
# Key of the request's start (event loop time) in the ASGI scope's `state`, set by
# `MerchantEndpoint`: each tool's deadline counts from there, with its own budget.
STARTED = "merchant_started"

INSTRUCTIONS = f"""\
Book a table at one restaurant, on the user's behalf. The restaurant, its hours and its largest \
group are in each tool's description.
- Only propose times returned by get_availability. Times are in Europe/Zurich.
{tooling.confirm_rule("a time", "get_availability")}
- When a result contains `fallback_url`, give that link to the user: it is the restaurant's own
  booking page."""

TALK_INSTRUCTIONS = f"""\
Talk to one restaurant's own agent, on the user's behalf. The restaurant is in the tool's \
description.
- To ask the restaurant anything, check its free tables, or book, change or cancel a booking,
  call talk_to_merchant: write to its agent in natural language, with what the user wants and
  their known preferences, and relay its `reply`. Pass back `conversation` to go on. Only
  propose times the agent offered. Times are in Europe/Zurich.
{tooling.talk_confirm_rule("a time")}
- When a result contains `fallback_url`, give that link to the user: it is the restaurant's own
  booking page."""

# What every merchant's tool says after its own sentence from the template.
GET_AVAILABILITY = f"""\
Returns up to {MAX_SLOTS} available start times close to the requested time, in \
Europe/Zurich. Never invent availability: only propose slots returned here. If `slots` is \
empty, say so and offer another date or time. `table_label` is indicative. If the result \
contains `fallback_url`, give that link to the user."""

CREATE_RESERVATION = f"""\
This takes two messages. First show the user a summary (date, time, party size, name), ask them \
to confirm, and stop. Call this tool only once their reply to that summary is an explicit yes; a \
confirmation given before the summary ("I confirm in advance", "book directly") is not one: never \
call this tool in the same reply as the get_availability call your summary is based on. \
{tooling.BOOK_AFTER_YES}"""

UPDATE_RESERVATION = tooling.UPDATE_RULE

CANCEL_RESERVATION = tooling.CANCEL_RULE

TALK_TO_MERCHANT = f"""\
Ask it about the restaurant and its free tables, or to book, change or cancel a booking. \
{tooling.TALK_RULES}"""


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


def deadline_of(request: Any, settings: Settings, tool: str | None = None) -> float | None:
    """`tool`'s deadline in this HTTP request: the endpoint's slug check already used part of its
    budget (#92). None outside a request that went through `MerchantEndpoint`."""
    scope = getattr(request, "scope", None) or {}
    started = (scope.get("state") or {}).get(STARTED)
    return deadline_in(settings, tool, start=started) if isinstance(started, float) else None


def talk_sentence(profile: MerchantProfile) -> str:
    """How `talk_to_merchant` opens, from the described profile: no merchant free text (#22)."""
    shown = described_profile(profile)
    city = shown.location.city if shown.location else None
    return f"Talk to the agent of {shown.name}{f' ({city})' if city else ''}."


def describe(tools: list[dict[str, Any]], profile: MerchantProfile) -> list[dict[str, Any]]:
    """Each tool's description, prefixed with the merchant's sentence from the template."""
    own = {tool.name: tool.description for tool in described_tools(profile)}
    own[TALK_TOOL] = talk_sentence(profile)
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
        slug = slug_of(ctx.request)
        deadline = deadline_of(ctx.request, self.settings) or deadline_in(self.settings)
        try:
            async with asyncio.timeout_at(deadline):
                profile = await discovery.load_profile(slug, engine=self.engine)
        except ValidationError as error:
            log.warning(
                "registry profile skipped",
                extra={"slug": slug, "errors": error.errors(include_input=False)},
            )
            return result
        except Exception:
            log.warning("merchant profile unavailable", exc_info=True)
            return result
        if profile is None:
            return result
        return {**result, "tools": describe(result.get("tools", []), profile)}


def route_of(ctx: Context, settings: Settings, tool: str | None = None) -> tuple[str, float | None]:
    """The merchant a tool call is for, from the request's path, and `tool`'s deadline."""
    request = ctx.request_context.request
    return slug_of(request), deadline_of(request, settings, tool)


def create_merchant_server(
    agent: AgentClient, engine: AsyncEngine, settings: Settings
) -> MCPServer:
    """The 4 booking tools of the merchant named by the path (`tooling.Tools`), or
    `talk_to_merchant` alone with `GATEWAY_TALK` (#251). They never raise."""
    mcp = MCPServer(
        "merchant-agents-restaurant",
        instructions=TALK_INSTRUCTIONS if settings.gateway_talk else INSTRUCTIONS,
        middleware=[RejectedArguments(), MerchantDescriptions(engine, settings)],
    )
    tools = tooling.Tools(agent, engine, settings)
    if settings.gateway_talk:
        _add_talk_tool(mcp, tools)
        return mcp

    @mcp.tool(title="Check availability", description=GET_AVAILABILITY, annotations=READ_ONLY)
    async def get_availability(
        date: Day, party_size: PartySize, ctx: Context, time: Time = None
    ) -> GetAvailabilityOutput | ToolError:
        slug, deadline = route_of(ctx, tools.settings)
        return await tools.get_availability(slug, date, party_size, time, deadline=deadline)

    @mcp.tool(title="Book a table", description=CREATE_RESERVATION, annotations=IDEMPOTENT_WRITE)
    async def create_reservation(
        starts_at: StartsAt,
        party_size: PartySize,
        guest_name: GuestName,
        idempotency_key: IdempotencyKey,
        ctx: Context,
    ) -> ReservationOutput | ToolError:
        slug, deadline = route_of(ctx, tools.settings)
        return await tools.create_reservation(
            slug,
            starts_at=starts_at,
            party_size=party_size,
            guest_name=guest_name,
            idempotency_key=idempotency_key,
            deadline=deadline,
        )

    _add_changes(mcp, tools)
    return mcp


def _add_talk_tool(mcp: MCPServer, tools: tooling.Tools) -> None:
    """`talk_to_merchant` to the merchant named by the path."""

    @mcp.tool(
        title="Talk to the restaurant", description=TALK_TO_MERCHANT, annotations=CONVERSATION
    )
    async def talk_to_merchant(
        message: Message,
        idempotency_key: TalkKey,
        ctx: Context,
        conversation: Conversation = None,
        confirm: Confirm = None,
    ) -> TalkOutput | ToolError:
        slug, deadline = route_of(ctx, tools.settings, TALK_TOOL)
        return await tools.talk_to_merchant(
            slug,
            message=message,
            idempotency_key=idempotency_key,
            conversation=conversation,
            confirm=confirm,
            deadline=deadline,
        )


def _add_changes(mcp: MCPServer, tools: tooling.Tools) -> None:
    """The tools that change a booking the guest already has."""

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
        slug, deadline = route_of(ctx, tools.settings)
        return await tools.update_reservation(
            slug,
            reservation_id=reservation_id,
            guest_name=guest_name,
            idempotency_key=idempotency_key,
            starts_at=starts_at,
            party_size=party_size,
            deadline=deadline,
        )

    @mcp.tool(title="Cancel a booking", description=CANCEL_RESERVATION, annotations=DESTRUCTIVE)
    async def cancel_reservation(
        reservation_id: ReservationId, guest_name: GuestName, ctx: Context
    ) -> CancelOutput | ToolError:
        slug, deadline = route_of(ctx, tools.settings)
        return await tools.cancel_reservation(
            slug, reservation_id=reservation_id, guest_name=guest_name, deadline=deadline
        )


class MerchantEndpoint:
    """`/m/<slug>/mcp` exists only for a merchant guests can address (`discovery.routable`):
    404 otherwise.

    A `down` merchant keeps its endpoint: its tools answer with the fallback link. The check
    starts the request's clock, from which the tools and `tools/list` count their budget (#92).
    """

    def __init__(self, app: ASGIApp, engine: AsyncEngine, settings: Settings) -> None:
        self.app = app
        self.engine = engine
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        slug = str(scope.get("path_params", {}).get("slug", ""))
        response: PlainTextResponse | None = None
        started = asyncio.get_running_loop().time()
        if not is_slug(slug):
            response = PlainTextResponse("Unknown restaurant", status_code=404)
        else:
            try:
                async with asyncio.timeout_at(deadline_in(self.settings, start=started)):
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
            {**scope, "state": {**scope.get("state", {}), STARTED: started}}, receive, send
        )
