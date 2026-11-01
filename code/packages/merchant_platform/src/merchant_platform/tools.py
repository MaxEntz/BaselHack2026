"""Admin tools of the platform assistant (AGENTS.md §8.2): runs the model's tool calls.

What the model reads about each tool is `specs.py`'s; the tools on the merchant's agent are
`bookings.py`'s, the look's `look_tool.py`'s. This module checks a call, dispatches it by tool
name, logs it and turns its outcome or its error into the model's result. Never the arguments
or the result in the logs: they hold guest names (`list_reservations`).
"""

import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import date
from typing import cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

from agent.service import AgentService
from builder.build import AlreadyProvisioned
from builder.llm import ToolCall
from core.errors import AgentError, AgentUnavailable, InvalidInput
from core.models import (
    BuildAgentInput,
    BuildAgentOutput,
    LinkPageInput,
    MerchantStatus,
    PageCard,
    PageCardData,
    ProfileCard,
    PublishAgentInput,
    ShowViewInput,
    SitePageEvent,
    StepEvent,
    ToolStartEvent,
    UpdateLookInput,
    ViewCard,
    ViewCardData,
)
from merchant_platform.accounts import IssuedLink
from merchant_platform.bookings import BookingTools
from merchant_platform.logs import traceback_without
from merchant_platform.look_tool import LookTool, SiteLook
from merchant_platform.registry import RegistryLookup, SaveLook
from merchant_platform.sessions import Emit, Session
from merchant_platform.specs import (
    ALREADY_ONBOARDED,
    BY_NAME,
    NOT_AVAILABLE,
    NOT_ONBOARDED,
    PAGE_LINKS,
    PUBLISHED,
    SITE_TAKEN,
    TOOL_FAILED,
    UNREVIEWED,
    VIEWS,
    Outcome,
    ToolResult,
    confirmation,
    failed,
)

log = logging.getLogger("merchant_platform.tools")

# Runs the builder for the merchant's website and description, streaming its steps.
BuildAgent = Callable[
    [BuildAgentInput, Callable[[StepEvent | SitePageEvent], Awaitable[None]]],
    Awaitable[BuildAgentOutput],
]
# How long after a build its browser may claim the owner cookie (`POST /session/claim`).
CLAIM_SECONDS = 300
# The secret link of a merchant just built, for its owner (#132).
IssueLink = Callable[[str], Awaitable[IssuedLink]]
# Publishes a merchant a build left waiting, streaming the `deploy` step (#136).
Publish = Callable[[str, Emit], Awaitable[None]]

# Runs one tool once `AdminTools._run` has validated its arguments against the tool's `input`.
Handler = Callable[[Session, BaseModel, Emit], Awaitable[Outcome]]


def session_tool[In: BaseModel](run: Callable[[Session, In, Emit], Awaitable[Outcome]]) -> Handler:
    """`run`, whose input type is its tool's `input` (the arguments were validated as one)."""

    async def handle(session: Session, arguments: BaseModel, emit: Emit) -> Outcome:
        return await run(session, cast(In, arguments), emit)

    return handle


def merchant_tool[In: BaseModel](run: Callable[[str, In], Awaitable[Outcome]]) -> Handler:
    """`run` on the session's merchant: every tool but `build_agent` needs one (`_run`)."""

    async def handle(session: Session, arguments: In, emit: Emit) -> Outcome:
        assert session.merchant_slug is not None
        return await run(session.merchant_slug, arguments)

    return session_tool(handle)


async def link_page(_: str, arguments: LinkPageInput) -> Outcome:
    return Outcome(
        {"shown_to_merchant": "page link", "text": PAGE_LINKS[arguments.page]},
        PageCard(data=PageCardData(page=arguments.page)),
    )


async def show_view(_: str, arguments: ShowViewInput) -> Outcome:
    return Outcome(
        {
            "shown_to_merchant": arguments.view,
            "moment": arguments.moment,
            "text": VIEWS[arguments.view],
        },
        ViewCard(data=ViewCardData(view=arguments.view, moment=arguments.moment)),
    )


def merchant_spoke_since_build(session: Session) -> bool:
    """A session that built its merchant publishes only after a merchant message: the model
    can't publish in the turn that built, before the review card was read (#152)."""
    if session.built_at is None:
        return True
    return any(m.get("role") == "user" for m in session.messages[session.built_at :])


class AdminTools:
    """Runs the model's tool calls for one merchant session."""

    def __init__(
        self,
        agent: AgentService | None,
        build_agent: BuildAgent,
        tz: ZoneInfo,
        today: Callable[[], date],
        *,
        owned_merchant: str | None = None,
        issue_link: IssueLink | None = None,
        publish: Publish | None = None,
        merchant: RegistryLookup | None = None,
        save_look: SaveLook | None = None,
        site_look: SiteLook | None = None,
    ) -> None:
        """`owned_merchant` (PLATFORM_MERCHANT) is the only existing merchant a session may
        take over by onboarding its website again (development only, §13, §15).
        `issue_link` gives a new merchant its owner's secret link (#132)."""
        self._build_agent = build_agent
        self._owned_merchant = owned_merchant
        self._issue_link = issue_link
        self._publish = publish
        self._merchant = merchant
        self._look = LookTool(merchant, save_look, site_look)
        bookings = BookingTools(agent, tz, today, self._status)
        self._handlers: dict[str, Handler] = {
            "build_agent": session_tool(self._build),
            "publish_agent": session_tool(self._publish_agent),
            "update_rules": merchant_tool(bookings.update_rules),
            "try_request": merchant_tool(bookings.try_request),
            "send_test_booking": merchant_tool(bookings.send_test_booking),
            "update_look": session_tool(self._update_look),
            "show_view": merchant_tool(show_view),
            "link_page": merchant_tool(link_page),
            "list_reservations": merchant_tool(bookings.list_reservations),
            "get_stats": merchant_tool(bookings.get_stats),
            "block_slot": merchant_tool(bookings.block_slot),
            "unblock_slot": merchant_tool(bookings.unblock_slot),
            "update_policy": merchant_tool(bookings.update_policy),
            "update_tables": merchant_tool(bookings.update_tables),
            "cancel_reservation": merchant_tool(bookings.cancel),
        }

    async def run(self, session: Session, call: ToolCall, emit: Emit) -> ToolResult:
        """Runs one call and logs it (§12): tool, merchant, latency, and `ok` or the error code."""
        started = time.perf_counter()
        outcome = "cancelled"
        try:
            result = await self._run(session, call, emit)
            outcome = result.error or "ok"
            return result
        finally:
            log.info(
                "admin_tool",
                extra={
                    "tool": call.name if call.name in BY_NAME else "unknown",
                    "merchant": session.merchant_slug,
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                    "outcome": outcome,
                },
            )

    async def _run(self, session: Session, call: ToolCall, emit: Emit) -> ToolResult:
        tool = BY_NAME.get(call.name)
        if tool is None:
            return failed("invalid_input", f"Unknown tool {call.name!r}.")
        await emit(ToolStartEvent(tool=tool.name, label=tool.label))
        if tool.name == "build_agent" and session.merchant_slug is not None:
            return failed("invalid_input", ALREADY_ONBOARDED)
        if tool.name != "build_agent" and session.merchant_slug is None:
            return failed("invalid_input", NOT_ONBOARDED)
        try:
            arguments = tool.input.model_validate_json(call.arguments or "{}")
        except ValidationError as error:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}"
                for e in error.errors(include_url=False)
            )
            return failed("invalid_input", f"invalid_input: {problems}")
        try:
            outcome = await self._handlers[tool.name](session, arguments, emit)
        except AlreadyProvisioned as error:
            if error.slug != self._owned_merchant:
                # Typing someone's URL must not hand over their bookings and settings.
                return failed(error.code, f"{error.code}: {SITE_TAKEN}")
            # The demo's merchant onboards again: work on its existing agent.
            session.merchant_slug = error.slug
            return failed(error.code, f"{error.code}: {error}")
        except AgentError as error:
            return failed(error.code, f"{error.code}: {error}")
        except Exception as error:
            # As `chat_turn_failed`: the traceback without the session's id (#220).
            log.error(
                "tool_failed",
                extra={"tool": tool.name, "exc": traceback_without(error, session.id)},
            )
            return failed("agent_unavailable", TOOL_FAILED)
        if outcome.card is not None:
            await emit(outcome.card)
        return ToolResult(json.dumps(outcome.data, default=str, ensure_ascii=False))

    async def _build(self, session: Session, arguments: BuildAgentInput, emit: Emit) -> Outcome:
        output = await self._build_agent(arguments, emit)
        # The link first: if it can't be stored, the session isn't attached to a merchant
        # nobody owns (`make owner-link` then gives it one).
        link = await self._issue_link(output.profile.slug) if self._issue_link else None
        session.merchant_slug = output.profile.slug
        session.built_at = len(session.messages)
        session.link = link.fingerprint if link else None
        # The building browser may claim its owner cookie once, shortly after (#132).
        session.claimable_until = time.monotonic() + CLAIM_SECONDS if link else None
        # Never the facts: their values and quotes are the crawled site's own words, which must
        # not reach a model that holds write tools (ARCHITECTURE.md §4). It reads the flags,
        # which code writes; the merchant sees the facts on the card.
        data = output.model_dump(mode="json", exclude={"tools", "facts"})
        # On the card only: the secret link never goes to the model or the history.
        return Outcome(
            {"shown_to_merchant": "profile card", **data},
            ProfileCard(data=output, login_url=link.url if link else None),
        )

    async def _publish_agent(self, session: Session, _: PublishAgentInput, emit: Emit) -> Outcome:
        if self._publish is None:
            raise AgentUnavailable(NOT_AVAILABLE)
        if not merchant_spoke_since_build(session):
            raise InvalidInput(UNREVIEWED)
        assert session.merchant_slug is not None
        await self._publish(session.merchant_slug, emit)
        return confirmation(PUBLISHED)

    async def _update_look(
        self, session: Session, arguments: UpdateLookInput, emit: Emit
    ) -> Outcome:
        assert session.merchant_slug is not None
        return await self._look(session.merchant_slug, arguments, emit)

    async def _status(self, slug: str) -> MerchantStatus | None:
        record = await self._merchant(slug) if self._merchant else None
        return record.status if record else None
