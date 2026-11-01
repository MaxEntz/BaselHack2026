"""Platform assistant: tool-use loop over the admin tools, for onboarding and daily operations.

Only validated data enters the prompt (the merchant's profile from the registry), never the
crawled website text.
"""

import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from functools import partial
from zoneinfo import ZoneInfo

from openai import AsyncOpenAI, Timeout
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.provisioning import generated_tools
from agent.service import AgentService
from agent.times import clock_line
from builder.build import BuildEmitter, Builder, Crawl, gateway_mcp_url, publish
from builder.cache import BuildCache
from builder.crawl import crawl
from builder.look import read_look
from core.config import Settings
from core.llm import LLMClient, LLMUnavailable, Message, OpenAIClient
from core.models import (
    BuildAgentInput,
    BuildAgentOutput,
    ErrorEvent,
    Look,
    MerchantProfile,
    MerchantStatus,
    StepEvent,
    TextEvent,
)
from merchant_platform.accounts import Accounts, IssuedLink, issued
from merchant_platform.logs import session_tag
from merchant_platform.registry import RegistryLookup, look_store, registry_records
from merchant_platform.sessions import Emit, RunTurn, Session
from merchant_platform.tool_specs import SPECS
from merchant_platform.tools import AdminTools

log = logging.getLogger("merchant_platform.assistant")

MAX_ITERATIONS = 8
# Tool results older than this many merchant messages are cut down when long.
KEEP_TURNS = 3
LONG_RESULT_CHARS = 600

UNAVAILABLE = "The assistant is temporarily unavailable, please retry."
# A model call that stops sending gives up after 60 s without a chunk (one retry, then
# UNAVAILABLE), rather than at the SDK's default 600 s: the web never gives up on its own, since
# it counts the platform's heartbeats as activity (#87). Streaming restarts the read timeout on
# every chunk, so a long answer still goes through.
LLM_TIMEOUT = Timeout(60, connect=5)
INTERRUPTED = "Interrupted before it finished."
TOO_MANY_STEPS = "I couldn't finish that request. Could you rephrase it or split it up?"

BASE_PROMPT = """\
You are the assistant of Sésame, a platform that turns a restaurant's website into a \
booking agent that Claude users can book through. You talk with the restaurant owner.

Rules:
- Reply in the merchant's language. Be brief and warm: one to three sentences per message.
- The interface shows tool results as cards (tables, figures, profile, confirmations). Never \
copy their content into your reply: comment on it in one or two sentences.
- Only state facts that come from tool results or from the merchant.
- Dates: work out "tonight", "tomorrow" or "Saturday" from today's date below. Times are local \
({tz}); pass them with the UTC offset given below for their date, never one you work out \
yourself.
"""

ONBOARDING_PROMPT = """\
Mode: onboarding. The merchant has no agent yet; only build_agent is available.
1. Greet them and say in one sentence what will happen: you read their website and their \
description, build an agent they check, then publish it so that Claude users can book them.
2. Ask for their website URL.
3. Ask, in a single message, for a short description of how they work: service hours, number \
of tables and their sizes, largest group bookable online, dietary options (vegetarian, vegan, \
gluten-free, halal, kosher), terrace, children, cancellation policy.
4. If an answer is vague, ask one targeted follow-up at most, then call build_agent with the \
URL and a description that sums up their answers.
A message that gives the URL together with anything about how they work, or that asks for the \
defaults, is enough: call build_agent at once, with no greeting and no question. The review \
card shows what you assumed, and the merchant corrects it there.
"""

OPERATIONS_PROMPT = """\
Mode: operations. The merchant has an agent.
{status}- Use the tools for bookings, figures, blocked times, the booking policy and the floor plan.
- Right after build_agent in this conversation, the review card is shown: call no other tool \
in that reply; read out its flags \
(each bold line and why) and ask the merchant to confirm or correct them. Corrections go \
through update_policy or update_tables. Once the merchant says it's right, call publish_agent \
(unless its status is already live), then wrap up with: "Your agent is live: Claude users can \
now book you."
- A merchant who asks to publish has checked: call publish_agent at once, without asking again.
- update_rules changes the cancellation policy; try_request shows what \
an agent gets for a party; send_test_booking books a test table as an agent would, only when \
asked; update_look changes the management site's colours, headings' font or corners; \
show_view shows Studio's agent or management pane, and with `moment` the management site at \
that time of day ("Show during service": view manage, moment during). Say what the tool's \
result says.
- Never change the merchant's own website. Asked to, answer exactly: "No. Sésame only reads \
your public site. It stays as it is, and it's where guests land when the agent can't help."
- A colour, font or shape request about the agent site: answer exactly: "Your agent site has \
no interface, so there's nothing to colour. It answers in JSON. I can change the management \
site."
- What agents read is the tool descriptions listed under "What agents read" below: quote them \
rather than describe them in your own words.
- The merchant also has a management site, with pages for Reservations, Guests, Opening hours \
(services and blocked times), Floor plan, Booking rules (largest group, cancelling), Agent \
and Settings. When a reply calls no other tool, because you answer from what you already \
know (opening hours, tables, rules) or because only a page can do it (resuming a paused \
agent), call link_page for that page, once, and don't name the page yourself. Never call \
link_page together with another tool.
- Before cancel_reservation, ask the merchant to confirm that booking explicitly, then cancel. \
If its result says the guest won't be notified, say why in a few words.

The merchant's validated profile:
{profile}
"""

# The registry's status, read before every model call: the Publish button and the Agent page's
# pause switch change it outside the conversation, so the history can be out of date (#198).
STATUS_LINES: dict[MerchantStatus, str] = {
    "building": "Status now: not published. Guests and their agents don't see it until "
    "publish_agent.",
    "live": "Status now: live. Claude users can book it; it needs no publishing.",
    "down": "Status now: paused. Guests and their agents get the merchant's own booking page "
    "instead. Only the pause switch on the Agent page resumes it (no tool does, publish_agent "
    "neither): when asked to resume, send the merchant there.",
}
STATUS_RULE = (
    "The status can change outside this conversation (the Publish button, the Agent page's "
    "pause switch): this line is current and wins over anything said earlier."
)


def system_prompt(
    now: datetime, profile: MerchantProfile | None, status: MerchantStatus | None = None
) -> str:
    tz = str(now.tzinfo)
    parts = [BASE_PROMPT.format(tz=tz), clock_line(now)]
    if profile is None:
        parts.append(ONBOARDING_PROMPT)
    else:
        line = f"{STATUS_LINES[status]} {STATUS_RULE}\n" if status else ""
        parts.append(
            OPERATIONS_PROMPT.format(status=line, profile=profile.model_dump_json(indent=1))
        )
        # Code-written from the template, merchant free text left out (#23): what agents read.
        read = "\n".join(f"- {t.name}: {t.description}" for t in generated_tools(profile))
        parts.append(f"What agents read:\n{read}")
    return "\n".join(parts)


def close_dangling_calls(history: Sequence[Message]) -> list[Message]:
    """Give every tool call an output: the API rejects a call left unanswered.

    A turn cancelled while a tool runs (the client left mid-`build_agent`) or a crash leaves
    one behind; without this, every later message in the session would fail.
    """
    answered = {message["call_id"] for message in history if message["role"] == "tool"}
    closed: list[Message] = []
    for message in history:
        closed.append(message)
        if message["role"] != "assistant":
            continue
        closed.extend(
            {"role": "tool", "call_id": call["id"], "content": INTERRUPTED, "is_error": True}
            for call in message.get("tool_calls", ())
            if call["id"] not in answered
        )
    return closed


def compact(history: Sequence[Message]) -> list[Message]:
    """Keep the history small: long tool results from old turns are replaced by a stub."""
    user_turns = [i for i, message in enumerate(history) if message["role"] == "user"]
    cutoff = user_turns[-KEEP_TURNS] if len(user_turns) >= KEEP_TURNS else 0
    compacted = []
    for index, message in enumerate(history):
        if (
            index < cutoff
            and message["role"] == "tool"
            and len(message["content"]) > LONG_RESULT_CHARS
        ):
            message = {**message, "content": "(older result omitted)"}
        compacted.append(message)
    return compacted


class Assistant:
    """`RunTurn` implementation: answers one merchant message."""

    def __init__(
        self,
        llm: LLMClient,
        tools: AdminTools,
        merchants: RegistryLookup,
        tz: ZoneInfo,
        *,
        default_merchant: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """`merchants` reads the registry row the prompt is built from: the merchant's profile
        and status, in one query."""
        self._llm = llm
        self._tools = tools
        self._merchants = merchants
        self._default_merchant = default_merchant
        self._clock = clock or (lambda: datetime.now(tz))

    async def __call__(self, session: Session, user_message: str, emit: Emit) -> None:
        if session.merchant_slug is None and self._default_merchant:
            # A session without a merchant takes PLATFORM_MERCHANT once it is registered (#88).
            attached, _ = await self._merchant(self._default_merchant)
            session.merchant_slug = attached.slug if attached else None
        session.messages.append({"role": "user", "content": user_message})

        async def on_text(delta: str) -> None:
            await emit(TextEvent(delta=delta))

        for _ in range(MAX_ITERATIONS):
            # Rebuilt every iteration: build_agent switches onboarding to operations mid-turn,
            # and the status may have changed since the last call (#198).
            slug = session.merchant_slug
            profile, status = await self._merchant(slug) if slug else (None, None)
            try:
                reply = await self._llm.respond(
                    instructions=system_prompt(self._clock(), profile, status),
                    tools=SPECS,
                    history=compact(close_dangling_calls(session.messages)),
                    on_text=on_text,
                )
            except LLMUnavailable as error:
                log.warning("assistant_unavailable", extra={"error": str(error)})
                await emit(ErrorEvent(message=UNAVAILABLE))
                return
            session.messages.append(reply.as_message())
            if not reply.tool_calls:
                return
            for call in reply.tool_calls:
                result = await self._tools.run(session, call, emit)
                session.messages.append(
                    {
                        "role": "tool",
                        "call_id": call.id,
                        "content": result.content,
                        "is_error": result.is_error,
                    }
                )
        log.warning("assistant_iteration_limit", extra={"session": session_tag(session.id)})
        await emit(ErrorEvent(message=TOO_MANY_STEPS))

    async def _merchant(self, slug: str) -> tuple[MerchantProfile | None, MerchantStatus | None]:
        """The merchant's validated profile and its status; neither for an unknown merchant."""
        record = await self._merchants(slug)
        profile = record.profile() if record else None
        return profile, record.status if record and profile else None


async def run_turn(session: Session, user_message: str, emit: Emit) -> None:
    """Stand-in when no LLM is configured (no OPENAI_API_KEY)."""
    await emit(TextEvent(delta="The platform assistant needs an OpenAI API key (OPENAI_API_KEY)."))


async def _no_steps(_: StepEvent) -> None:
    """A look read again isn't a build: no steps to show."""


def default_assistant(
    settings: Settings,
    engine: AsyncEngine,
    agent: AgentService | None = None,
    accounts: Accounts | None = None,
    *,
    llm: LLMClient | None = None,
    crawler: Crawl | None = None,
) -> RunTurn:
    """The assistant wired to OpenAI, the registry, the builder and the owners' links.

    `llm` stands in for OpenAI, in the conversation and the extraction alike, and `crawler`
    for the builder's crawl, in a build and in a look read again: the browser test of the
    whole stack runs both as fakes (#153). Without `crawler`, the builder's checked crawl,
    which reads the homepage only for a look.
    """
    clients = (llm, llm) if llm is not None else _openai_clients(settings)
    if clients is None:
        return run_turn
    chat, extractor = clients
    tz = ZoneInfo(settings.merchant_tz)
    merchants = registry_records(engine)
    builder = _builder(settings, engine, extractor, crawler, tz)
    tools = AdminTools(
        agent,
        partial(_build, builder),
        tz,
        today=lambda: datetime.now(tz).date(),
        owned_merchant=settings.platform_merchant,
        issue_link=partial(_issue_link, settings, accounts) if accounts else None,
        publish=partial(publish, engine=engine),
        merchant=merchants,
        save_look=look_store(engine),
        site_look=partial(_site_look, merchants, crawler or partial(crawl, max_pages=0)),
    )
    return Assistant(chat, tools, merchants, tz, default_merchant=settings.platform_merchant)


def _openai_clients(settings: Settings) -> tuple[LLMClient, LLMClient] | None:
    """The conversation's client and the extraction's, on one OpenAI connection; None without
    OPENAI_API_KEY."""
    if settings.openai_api_key is None:
        return None
    # Retries are ours (one, before any text reached the merchant).
    openai = AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(), max_retries=0, timeout=LLM_TIMEOUT
    )
    chat = OpenAIClient(
        openai, settings.openai_model, reasoning_effort=settings.openai_reasoning_effort
    )
    # Spike #4: no reasoning extracts as well, a second faster.
    extractor = OpenAIClient(
        openai, settings.openai_model, reasoning_effort=settings.builder_reasoning_effort
    )
    return chat, extractor


def _builder(
    settings: Settings, engine: AsyncEngine, llm: LLMClient, crawler: Crawl | None, tz: ZoneInfo
) -> Builder:
    return Builder(
        llm,
        engine,
        model=settings.openai_model,
        mcp_url=gateway_mcp_url(settings.gateway_public_url),
        cache=BuildCache(settings.builder_cache),
        crawl=crawler or crawl,
        tz=tz,
    )


async def _build(
    builder: Builder, arguments: BuildAgentInput, emit: BuildEmitter
) -> BuildAgentOutput:
    return await builder.build_agent(str(arguments.website), arguments.description, emit)


async def _issue_link(settings: Settings, accounts: Accounts, slug: str) -> IssuedLink:
    """A new merchant's secret link, for its owner (#132)."""
    return issued(settings.platform_web_url, await accounts.issue_link_token(slug))


async def _site_look(merchants: RegistryLookup, homepage: Crawl, slug: str) -> Look | None:
    """The merchant website's own look, read again (#152's "back to the look from your
    site"): the homepage only, through the builder's checked crawl or `crawler` (#220)."""
    record = await merchants(slug)
    if record is None:
        return None
    crawled = await homepage(record.website, _no_steps)
    return read_look(crawled.pages[0].look)[0]
