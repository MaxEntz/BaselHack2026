"""Platform assistant: tool-use loop over the admin tools, for onboarding and daily operations.

Only validated data enters the prompt (the merchant's profile from the registry), never the
crawled website text.
"""

import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from openai import AsyncOpenAI, Timeout
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.service import AgentService
from builder.build import Builder, gateway_mcp_url
from builder.cache import BuildCache
from builder.crawl import StepEmitter
from builder.llm import LLMClient, LLMUnavailable, Message, OpenAIClient
from core.config import Settings
from core.db import merchants_t
from core.models import (
    BuildAgentInput,
    BuildAgentOutput,
    ErrorEvent,
    MerchantProfile,
    TextEvent,
)
from merchant_platform.sessions import Session
from merchant_platform.tools import SPECS, AdminTools, Emit

log = logging.getLogger("merchant_platform.assistant")

RunTurn = Callable[[Session, str, Emit], Awaitable[None]]
ProfileLookup = Callable[[str], Awaitable[MerchantProfile | None]]

MAX_ITERATIONS = 8
# Tool results older than this many merchant messages are cut down when long.
KEEP_TURNS = 3
LONG_RESULT_CHARS = 600

UNAVAILABLE = "The assistant is temporarily unavailable, please retry."
# A model call that stops sending gives up after 60 s without a chunk (one retry, then
# UNAVAILABLE). The web no longer does: it counts the platform's heartbeats as activity (#87),
# and the SDK's default read timeout is 600 s. Streaming restarts the read timeout on every
# chunk, so a long answer still goes through.
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
description, then publish an agent that Claude users can book through.
2. Ask for their website URL.
3. Ask, in a single message, for a short description of how they work: service hours, number \
of tables and their sizes, largest group bookable online, dietary options (vegetarian, vegan, \
gluten-free, halal, kosher), terrace, children, cancellation policy.
4. If an answer is vague, ask one targeted follow-up at most, then call build_agent with the \
URL and a description that sums up their answers.
"""

OPERATIONS_PROMPT = """\
Mode: operations. The merchant's agent is live.
- Use the tools for bookings, figures, blocked times, the booking policy and the floor plan.
- Right after build_agent in this conversation, the profile card is shown: list the \
assumptions from its result (default values used) and ask the merchant to confirm or correct \
them. Corrections go through update_policy or update_tables. Then wrap up with: "Your agent \
is live: Claude users can now book you."
- Before cancel_reservation, ask the merchant to confirm that booking explicitly, then cancel. \
If its result says the guest won't be notified, say why in a few words.

The merchant's validated profile:
{profile}
"""


# How far ahead the prompt looks for the next clock change.
OFFSET_HORIZON_DAYS = 400


def next_offset_change(now: datetime) -> datetime | None:
    """Noon of the first local day, from today, whose UTC offset differs from `now`'s.

    Today counts: before 03:00 on the night of a switch, tonight's services are already in
    the new offset. None when the time zone keeps one offset over the horizon.
    """
    for days in range(OFFSET_HORIZON_DAYS + 1):
        day = datetime.combine(now.date() + timedelta(days=days), time(12), now.tzinfo)
        if day.utcoffset() != now.utcoffset():
            return day
    return None


def clock_line(now: datetime) -> str:
    """Today's date, time and UTC offset, and the next offset change (#83).

    The admin tools take times with their offset; a model guessing it would send a summer
    offset after the switch, and `block_slot` would block another hour without failing.
    """
    tz = str(now.tzinfo)
    line = f"Today is {now:%A %Y-%m-%d}, it is {now:%H:%M} ({tz}, UTC{now:%:z})."
    change = next_offset_change(now)
    if change is not None:
        line += (
            f" The offset becomes UTC{change:%:z} on {change:%A %Y-%m-%d}: use it for times"
            " from that date on."
        )
    return line + "\n"


def system_prompt(now: datetime, profile: MerchantProfile | None) -> str:
    tz = str(now.tzinfo)
    parts = [BASE_PROMPT.format(tz=tz), clock_line(now)]
    if profile is None:
        parts.append(ONBOARDING_PROMPT)
    else:
        parts.append(OPERATIONS_PROMPT.format(profile=profile.model_dump_json(indent=1)))
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


async def session_merchant(
    session: Session | None, default: str | None, profiles: ProfileLookup
) -> MerchantProfile | None:
    """The merchant `session` works on, or will from its next message (#88).

    A session without a merchant takes `default` (PLATFORM_MERCHANT) once it is registered,
    so that a page reload or a seeded demo doesn't force a new onboarding (AGENTS.md §14).
    """
    slug = (session.merchant_slug if session else None) or default
    return await profiles(slug) if slug else None


class Assistant:
    """`RunTurn` implementation: answers one merchant message."""

    def __init__(
        self,
        llm: LLMClient,
        tools: AdminTools,
        profiles: ProfileLookup,
        tz: ZoneInfo,
        *,
        default_merchant: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._profiles = profiles
        self._default_merchant = default_merchant
        self._clock = clock or (lambda: datetime.now(tz))

    async def __call__(self, session: Session, user_message: str, emit: Emit) -> None:
        if session.merchant_slug is None:
            attached = await session_merchant(session, self._default_merchant, self._profiles)
            session.merchant_slug = attached.slug if attached else None
        session.messages.append({"role": "user", "content": user_message})

        async def on_text(delta: str) -> None:
            await emit(TextEvent(delta=delta))

        for _ in range(MAX_ITERATIONS):
            # Rebuilt every iteration: build_agent switches onboarding to operations mid-turn.
            profile = await self._profiles(session.merchant_slug) if session.merchant_slug else None
            try:
                reply = await self._llm.respond(
                    instructions=system_prompt(self._clock(), profile),
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
        log.warning("assistant_iteration_limit", extra={"session": session.id})
        await emit(ErrorEvent(message=TOO_MANY_STEPS))


def registry_profiles(engine: AsyncEngine) -> ProfileLookup:
    """The validated profile stored in the registry, or None for an unknown merchant."""

    async def lookup(slug: str) -> MerchantProfile | None:
        async with engine.connect() as conn:
            stored = await conn.scalar(
                select(merchants_t.c.profile).where(merchants_t.c.slug == slug)
            )
        if stored is None:
            return None
        return MerchantProfile.model_validate_json(json.dumps(stored))

    return lookup


async def run_turn(session: Session, user_message: str, emit: Emit) -> None:
    """Stand-in when no LLM is configured (no OPENAI_API_KEY)."""
    await emit(TextEvent(delta="The platform assistant needs an OpenAI API key (OPENAI_API_KEY)."))


def default_assistant(
    settings: Settings, engine: AsyncEngine, agent: AgentService | None = None
) -> RunTurn:
    """The assistant wired to OpenAI, the registry and the builder."""
    if settings.openai_api_key is None:
        return run_turn
    tz = ZoneInfo(settings.merchant_tz)
    # Retries are ours (one, before any text reached the merchant).
    openai = AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(), max_retries=0, timeout=LLM_TIMEOUT
    )
    llm = OpenAIClient(
        openai, settings.openai_model, reasoning_effort=settings.openai_reasoning_effort
    )
    builder = Builder(
        # Spike #4: no reasoning extracts as well, a second faster.
        OpenAIClient(
            openai, settings.openai_model, reasoning_effort=settings.builder_reasoning_effort
        ),
        engine,
        model=settings.openai_model,
        mcp_url=gateway_mcp_url(settings.gateway_public_url),
        cache=BuildCache(settings.builder_cache),
    )

    async def build(arguments: BuildAgentInput, emit: StepEmitter) -> BuildAgentOutput:
        return await builder.build_agent(str(arguments.website), arguments.description, emit)

    tools = AdminTools(
        agent,
        build,
        tz,
        today=lambda: datetime.now(tz).date(),
        owned_merchant=settings.platform_merchant,
    )
    return Assistant(
        llm, tools, registry_profiles(engine), tz, default_merchant=settings.platform_merchant
    )
