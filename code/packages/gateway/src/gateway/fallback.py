"""Where agent failures become tool results: timeout, `down` status, fallback URL, call log.

A tool never raises: Claude would get a generic error without the link to the merchant's site.
Every outcome is a value Claude can read, and every call logs one `tool_call` line. Guest names
never appear in that line.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from pydantic import ValidationError

from agent.discovery import RegistryEntry, routable
from core.config import Settings, get_settings
from core.errors import AgentUnavailable, InvalidInput, NotFound, PartyTooLarge, SlotUnavailable
from core.models import ToolError

log = logging.getLogger("gateway.tools")

# Id of the BATP request behind the current tool call, to match this log line with the node's.
batp_request: ContextVar[str | None] = ContextVar("batp_request", default=None)

# A timeout can hit after the COMMIT: these calls may have succeeded although Claude sees an error.
WRITE_TOOLS = frozenset({"create_reservation", "update_reservation", "cancel_reservation"})
MAYBE_SAVED = (
    "The change may have gone through: check get_my_reservations before retrying, "
    "with the same idempotency_key if the tool takes one."
)
SERVICE_UNAVAILABLE = "The booking service is unavailable right now. Please try again in a moment."
# AGENT_TIMEOUT_MS bounds a whole tool call (#92). Only the fallback link of a search that ran
# out of time may go past it, by this share of the budget at most.
LINK_LOOKUP_SHARE = 0.1


def deadline_in(settings: Settings) -> float:
    """When a tool call starting now must have answered, in event loop time."""
    return asyncio.get_running_loop().time() + settings.agent_timeout_ms / 1000


def log_call(tool: str, merchant: str | None, started: float, result: object) -> None:
    """The structured line of one tool call; `outcome` is `ok` or the error code."""
    hop = batp_request.get()
    log.info(
        "tool_call",
        extra={
            "tool": tool,
            "merchant": merchant,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "outcome": result.error if isinstance(result, ToolError) else "ok",
            **({"batp_request": hop} if hop else {}),
        },
    )


def unavailable(merchant: RegistryEntry, detail: str = "") -> ToolError:
    """`agent_unavailable` with the link to the merchant's own booking page."""
    url = merchant.fallback_url
    parts = [
        f"{merchant.name} can't take bookings here right now.",
        detail,
        f"Book directly: {url}",
    ]
    return ToolError(
        error="agent_unavailable",
        message=" ".join(part for part in parts if part),
        fallback_url=url,
    )


def _invalid_input(error: ValidationError) -> ToolError:
    # Not str(error): it quotes the rejected values, and only field names are needed here.
    problems = "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or 'input'}: {item['msg']}"
        for item in error.errors(include_url=False, include_input=False, include_context=False)
    )
    return ToolError(error="invalid_input", message=problems)


def _to_error(exc: Exception, merchant: RegistryEntry, tool: str) -> ToolError:
    match exc:
        case PartyTooLarge():
            url = merchant.fallback_url
            return ToolError(
                error=exc.code, message=f"{exc}. Book directly: {url}", fallback_url=url
            )
        case SlotUnavailable() | NotFound() | InvalidInput():
            return ToolError(error=exc.code, message=str(exc))
        case ValidationError():
            return _invalid_input(exc)
        case TimeoutError() | AgentUnavailable():
            pass
        case _:
            log.error("tool_failed", extra={"tool": tool, "merchant": merchant.slug}, exc_info=exc)
    return unavailable(merchant, MAYBE_SAVED if tool in WRITE_TOOLS else "")


def _service_unavailable(exc: Exception, tool: str, merchant: str | None) -> ToolError:
    if not isinstance(exc, TimeoutError | AgentUnavailable):
        log.error("tool_failed", extra={"tool": tool, "merchant": merchant}, exc_info=exc)
    return ToolError(error="agent_unavailable", message=SERVICE_UNAVAILABLE)


def _chaos_delay(slug: str, settings: Settings) -> float:
    targets = {target.strip() for target in settings.chaos_merchants.split(",") if target.strip()}
    if settings.chaos_delay_ms and (not targets or slug in targets):
        return settings.chaos_delay_ms / 1000
    return 0


async def call_agent[T](
    merchant: RegistryEntry | None,
    tool: str,
    op: Callable[[], Awaitable[T]],
    *,
    settings: Settings | None = None,
    started: float | None = None,
    deadline: float | None = None,
) -> T | ToolError:
    """Run `op` for a merchant: the result, or the `ToolError` Claude should read instead.

    A merchant guests can't address (`discovery.routable`) is `not_found`, a `paused` one gets
    its fallback URL: neither reaches `op`. Cancellation (the client went away) is not an
    `Exception` and propagates. `started` counts earlier work in the logged latency, `deadline`
    (event loop time, default: a full budget from now) the time it already took.
    """
    settings = settings or get_settings()
    started = started or time.perf_counter()
    deadline = deadline or deadline_in(settings)

    async def attempt(target: RegistryEntry) -> T:
        if delay := _chaos_delay(target.slug, settings):
            await asyncio.sleep(delay)
        return await op()

    result: T | ToolError
    target = routable(merchant)
    if target is None:
        result = ToolError(
            error="not_found",
            message="Unknown restaurant: use a slug returned by search_merchants.",
        )
    elif target.paused:
        result = unavailable(target)
    else:
        try:
            async with asyncio.timeout_at(deadline):
                result = await attempt(target)
        except Exception as exc:
            result = _to_error(exc, target, tool)
    log_call(tool, merchant.slug if merchant else None, started, result)
    return result


async def call_merchant[T](
    slug: str,
    tool: str,
    find: Callable[[], Awaitable[RegistryEntry | None]],
    op: Callable[[], Awaitable[T]],
    *,
    settings: Settings | None = None,
    deadline: float | None = None,
) -> T | ToolError:
    """`call_agent` after looking the merchant up; without the registry there is no URL to give.

    The lookup and the agent share one deadline: `deadline`, or a full budget from now.
    """
    settings = settings or get_settings()
    started = time.perf_counter()
    deadline = deadline or deadline_in(settings)
    try:
        async with asyncio.timeout_at(deadline):
            merchant = await find()
    except Exception as exc:
        result = _service_unavailable(exc, tool, slug)
        log_call(tool, slug, started, result)
        return result
    return await call_agent(
        merchant, tool, op, settings=settings, started=started, deadline=deadline
    )


async def call_service[T](
    tool: str,
    op: Callable[[], Awaitable[T]],
    *,
    settings: Settings | None = None,
    named: Callable[[], Awaitable[RegistryEntry | None]] | None = None,
) -> T | ToolError:
    """`call_agent` for what no single merchant serves, so there is no fallback URL by default.

    `op` may return a `ToolError` itself, which is then logged as the call's outcome. A refusal
    keeps its own code: the service answered. Only when it can't answer may `named` still find
    the merchant the request is about, to give its link: within what is left of the budget, or
    `LINK_LOOKUP_SHARE` of it when `op` used it all.
    """
    settings = settings or get_settings()
    started = time.perf_counter()
    deadline = deadline_in(settings)
    result: T | ToolError
    try:
        async with asyncio.timeout_at(deadline):
            result = await op()
    except (SlotUnavailable, NotFound, InvalidInput) as exc:
        result = ToolError(error=exc.code, message=str(exc))
    except Exception as exc:
        result = _service_unavailable(exc, tool, None)
        loop = asyncio.get_running_loop()
        budget = max(deadline - loop.time(), settings.agent_timeout_ms / 1000 * LINK_LOOKUP_SHARE)
        if named and (merchant := await _quietly(named, budget)):
            result = unavailable(merchant)
    log_call(tool, None, started, result)
    return result


async def _quietly(
    find: Callable[[], Awaitable[RegistryEntry | None]], budget: float
) -> RegistryEntry | None:
    """A last resort: its own failure must not hide the one being reported."""
    try:
        return await asyncio.wait_for(find(), budget)
    except Exception:
        log.warning("registry unreachable", exc_info=True)
        return None


class RejectedArguments:
    """Arguments the SDK rejects before a tool runs get a §8.4 error and a log line as well.

    Only those end up as `isError` results: a tool body turns every failure into a `ToolError`.
    Middleware sees the result in its wire format, a dict.
    """

    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        started = time.perf_counter()
        result = await call_next(ctx)
        if ctx.method != "tools/call" or not isinstance(result, dict) or not result.get("isError"):
            return result
        lines = [
            line.strip()
            for block in result["content"]
            for line in block.get("text", "").splitlines()
        ]
        message = "; ".join(line for line in lines if line and "errors.pydantic.dev" not in line)
        error = ToolError(error="invalid_input", message=message)
        params = ctx.params if isinstance(ctx.params, dict) else {}
        log_call(str(params.get("name")), None, started, error)
        return {
            **result,
            "content": [{"type": "text", "text": error.model_dump_json(indent=2)}],
            "structuredContent": {"result": error.model_dump(mode="json")},
            "isError": False,
        }
