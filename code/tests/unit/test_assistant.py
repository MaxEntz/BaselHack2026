"""Platform assistant tool-use loop with a scripted LLM, a fake agent service and builder."""

import asyncio
import json
import logging
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, cast, get_args
from zoneinfo import ZoneInfo

import pytest
from openai import AsyncOpenAI
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from agent.service import AgentService, MerchantCancellation
from builder.build import AlreadyProvisioned
from builder.llm import (
    LLMClient,
    LLMReply,
    LLMUnavailable,
    Message,
    OnText,
    OpenAIClient,
    ToolCall,
    ToolSpec,
)
from core.config import Settings
from core.errors import NotFound
from core.models import (
    AdminReservationRow,
    BuildAgentInput,
    BuildAgentOutput,
    ChatEvent,
    GeneratedTool,
    MerchantCancelOutcome,
    MerchantProfile,
    Policy,
    ProfileCard,
    Reservation,
    Service,
    Stats,
    StepEvent,
    TableSpec,
)
from merchant_platform.accounts import IssuedLink
from merchant_platform.assistant import (
    INTERRUPTED,
    MAX_ITERATIONS,
    TOO_MANY_STEPS,
    UNAVAILABLE,
    Assistant,
    compact,
    default_assistant,
    system_prompt,
)
from merchant_platform.sessions import Session
from merchant_platform.tools import (
    CANCELLED,
    NOT_AVAILABLE,
    NOT_ONBOARDED,
    SITE_TAKEN,
    AdminTools,
)

TZ = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 10, 31, 18, 30, tzinfo=TZ)
TODAY = NOW.date()
FIXTURE = Path(__file__).parents[1] / "fixtures" / "le-petit-zinc.json"
PROFILE = MerchantProfile.model_validate_json(FIXTURE.read_text())
SLUG = PROFILE.slug


class ScriptedLLM:
    """Replays replies in order; a reply is (text chunks, tool calls)."""

    def __init__(self, *replies: tuple[Sequence[str], Sequence[ToolCall]]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def respond(
        self,
        *,
        instructions: str,
        tools: Sequence[ToolSpec],
        history: Sequence[Message],
        on_text: OnText,
    ) -> LLMReply:
        self.calls.append({"instructions": instructions, "history": list(history)})
        if not self.replies:
            raise AssertionError("unexpected LLM call")
        chunks, calls = self.replies.pop(0)
        for chunk in chunks:
            await on_text(chunk)
        return LLMReply(text="".join(chunks), tool_calls=tuple(calls))


class LoopingLLM(ScriptedLLM):
    """Asks for a tool on every call."""

    async def respond(self, **kwargs: Any) -> LLMReply:
        self.calls.append(kwargs)
        return LLMReply(text="", tool_calls=(call("get_stats", date="2026-10-31"),))


class DownLLM(ScriptedLLM):
    async def respond(self, **kwargs: Any) -> LLMReply:
        raise LLMUnavailable("InternalServerError")


# What cancelling each of FakeAgent's bookings does (#82).
OUTCOMES: dict[str, MerchantCancelOutcome] = {
    "R-001": "notified",
    "R-002": "ended",
    "R-003": "guest_cancelled",
    "R-004": "already_cancelled",
}


class FakeAgent:
    """In-memory merchant operations for SLUG."""

    def __init__(self) -> None:
        self.blocked: list[datetime] = []
        self.cancelled: list[str] = []
        self.rows = [
            AdminReservationRow(
                id="R-001",
                time="19:00",
                guest_name="Alex Martin",
                party_size=2,
                table_label="T1",
                via="website",
                status="confirmed",
            )
        ]

    async def list_reservations(self, slug: str, day: date) -> list[AdminReservationRow]:
        return self.rows if day == TODAY else []

    async def get_stats(self, slug: str, day: date) -> Stats:
        return Stats(bookings=1, guests=2, seats=44, blocked=())

    async def block_slot(self, slug: str, starts_at: datetime) -> None:
        self.blocked.append(starts_at)

    async def unblock_slot(self, slug: str, starts_at: datetime) -> None:
        self.blocked.remove(starts_at)

    async def update_policy(
        self,
        slug: str,
        max_party_size: int | None = None,
        services: Sequence[Service] | None = None,
    ) -> Policy:
        return Policy(
            max_party_size=max_party_size or PROFILE.max_party_size,
            slot_length_min=120,
            services=tuple(services or PROFILE.services),
        )

    async def update_tables(self, slug: str, tables: Sequence[TableSpec]) -> list[TableSpec]:
        return list(tables)

    async def cancel_by_merchant(self, slug: str, reservation_id: str) -> MerchantCancellation:
        outcome = OUTCOMES.get(reservation_id)
        if outcome is None:
            raise NotFound(f"No booking {reservation_id}")
        self.cancelled.append(reservation_id)
        return MerchantCancellation(
            reservation=await self.cancel_reservation(slug, reservation_id, by="merchant"),
            outcome=outcome,
        )

    async def cancel_reservation(
        self, slug: str, reservation_id: str, *, by: str, guest_name: str | None = None
    ) -> Reservation:
        if reservation_id not in OUTCOMES:
            raise NotFound(f"No booking {reservation_id}")
        return Reservation(
            id=reservation_id,
            merchant=slug,
            starts_at=NOW,
            party_size=2,
            table_label="T1",
            status="cancelled",
            guest_name="Alex Martin",
            via="website",
        )


async def fake_build(arguments: BuildAgentInput, emit: Any) -> BuildAgentOutput:
    for step in ("crawl", "extract", "validate", "provision", "deploy"):
        await emit(StepEvent(step=step, status="running"))  # type: ignore[arg-type]
        await emit(StepEvent(step=step, status="done", duration_ms=1))  # type: ignore[arg-type]
    return BuildAgentOutput(
        profile=PROFILE,
        status="live",
        assumptions=("Bookings last 120 minutes (default).",),
        tools=(GeneratedTool(name="get_availability", description="Free tables."),),
        mcp_url="http://localhost:8001/mcp",
    )


def call(name: str, call_id: str | None = None, **arguments: Any) -> ToolCall:
    return ToolCall(id=call_id or f"call-{name}", name=name, arguments=json.dumps(arguments))


class Harness:
    def __init__(self, llm: LLMClient, *, agent: FakeAgent | None = None) -> None:
        self.llm = llm
        self.agent = agent
        self.events: list[ChatEvent] = []
        self.provisioned: set[str] = set()
        tools = AdminTools(
            cast(AgentService, agent) if agent else None, self._build, TZ, today=lambda: TODAY
        )
        self.assistant = Assistant(llm, tools, self._profiles, TZ, clock=lambda: NOW)

    async def _build(self, arguments: BuildAgentInput, emit: Any) -> BuildAgentOutput:
        output = await fake_build(arguments, emit)
        self.provisioned.add(output.profile.slug)
        return output

    async def _profiles(self, slug: str) -> MerchantProfile | None:
        return PROFILE if slug in self.provisioned else None

    async def emit(self, event: ChatEvent) -> None:
        self.events.append(event)

    async def turn(self, session: Session, message: str) -> list[dict[str, Any]]:
        self.events.clear()
        await self.assistant(session, message, self.emit)
        return [json.loads(event.model_dump_json()) for event in self.events]


def onboarded() -> Session:
    session = Session(id="s")
    session.merchant_slug = SLUG
    return session


def kinds(events: list[dict[str, Any]]) -> list[str]:
    return [event.get("kind", event["type"]) for event in events]


# The loop


async def test_tool_use_then_text_streams_events_and_records_the_history() -> None:
    llm = ScriptedLLM(
        (["Let me check."], [call("list_reservations", date="2026-10-31")]),
        (["One booking ", "tonight."], []),
    )
    harness = Harness(llm, agent=FakeAgent())
    harness.provisioned.add(SLUG)
    session = onboarded()

    events = await harness.turn(session, "What bookings do I have tonight?")

    assert kinds(events) == ["text", "tool_start", "reservations", "text", "text"]
    assert events[1]["label"] == "Checking the bookings…"
    assert events[2]["data"]["reservations"][0]["id"] == "R-001"
    assert [message["role"] for message in session.messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    tool_message = session.messages[2]
    assert tool_message["call_id"] == "call-list_reservations"
    assert tool_message["is_error"] is False
    assert json.loads(tool_message["content"])["shown_to_merchant"] == "bookings table"
    # The second call sees the tool result.
    assert llm.calls[1]["history"][-1]["role"] == "tool"


async def test_the_loop_stops_after_8_iterations() -> None:
    llm = LoopingLLM()
    harness = Harness(llm, agent=FakeAgent())
    harness.provisioned.add(SLUG)

    events = await harness.turn(onboarded(), "Stats please")

    assert len(llm.calls) == MAX_ITERATIONS
    assert kinds(events).count("stats") == MAX_ITERATIONS
    assert events[-1] == {"type": "error", "message": TOO_MANY_STEPS}


async def test_a_tool_error_goes_back_to_the_model_and_the_loop_continues() -> None:
    llm = ScriptedLLM(
        ([], [call("cancel_reservation", reservation_id="R-404")]),
        (["I can't find R-404."], []),
    )
    harness = Harness(llm, agent=FakeAgent())
    harness.provisioned.add(SLUG)
    session = onboarded()

    events = await harness.turn(session, "Cancel R-404")

    assert kinds(events) == ["tool_start", "text"]
    result = session.messages[2]
    assert result["is_error"] is True
    assert result["content"].startswith("not_found:")


async def test_invalid_arguments_are_a_tool_error() -> None:
    llm = ScriptedLLM(([], [call("block_slot", starts_at="21:00")]), (["Which day?"], []))
    harness = Harness(llm, agent=FakeAgent())
    harness.provisioned.add(SLUG)
    session = onboarded()

    await harness.turn(session, "Block 21:00")

    assert session.messages[2]["is_error"] is True
    assert session.messages[2]["content"].startswith("invalid_input: starts_at")


@pytest.mark.parametrize("tool", ["block_slot", "unblock_slot"])
@pytest.mark.parametrize("starts_at", ["2026-10-31T21:00:00+02:00", "2026-10-31T21:00:00Z"])
async def test_a_slot_outside_the_local_offset_is_refused(tool: str, starts_at: str) -> None:
    """A summer offset after the switch would block another hour without failing (#83)."""
    agent = FakeAgent()
    agent.blocked.append(datetime(2026, 10, 31, 21, tzinfo=TZ))
    llm = ScriptedLLM(([], [call(tool, starts_at=starts_at)]), (["Sorry."], []))
    harness = Harness(llm, agent=agent)
    harness.provisioned.add(SLUG)
    session = onboarded()

    events = await harness.turn(session, "Block 21:00 tonight")

    result = session.messages[2]
    assert result["is_error"] is True
    assert result["content"] == (
        f"invalid_input: {starts_at} doesn't use the Europe/Zurich offset, which is UTC+01:00"
        " on 2026-10-31. For 21:00 local time, send 2026-10-31T21:00:00+01:00."
    ).replace(starts_at, datetime.fromisoformat(starts_at).isoformat())
    assert "card" not in kinds(events)
    assert agent.blocked == [datetime(2026, 10, 31, 21, tzinfo=TZ)]


@pytest.mark.parametrize(
    "starts_at",
    [
        "2026-10-31T21:00:00+01:00",
        "2027-07-01T21:00:00+02:00",
        # The hour that repeats on the night of the switch exists in both offsets.
        "2026-10-25T02:30:00+02:00",
        "2026-10-25T02:30:00+01:00",
    ],
)
async def test_a_slot_in_the_local_offset_is_blocked(starts_at: str) -> None:
    agent = FakeAgent()
    llm = ScriptedLLM(([], [call("block_slot", starts_at=starts_at)]), (["Done."], []))
    harness = Harness(llm, agent=agent)
    harness.provisioned.add(SLUG)
    session = onboarded()

    await harness.turn(session, "Block it")

    assert session.messages[2]["is_error"] is False
    assert agent.blocked == [datetime.fromisoformat(starts_at)]


async def test_a_turn_cancelled_during_a_tool_leaves_the_session_usable() -> None:
    started = asyncio.Event()

    class SlowAgent(FakeAgent):
        async def get_stats(self, slug: str, day: date) -> Stats:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    llm = ScriptedLLM(([], [call("get_stats", "call_1", date="2026-10-31")]), (["Hi."], []))
    harness = Harness(llm, agent=SlowAgent())
    harness.provisioned.add(SLUG)
    session = onboarded()

    turn = asyncio.create_task(harness.turn(session, "How busy?"))
    await asyncio.wait_for(started.wait(), 1)
    turn.cancel()  # the client left: /chat cancels the turn
    with pytest.raises(asyncio.CancelledError):
        await turn
    await harness.turn(session, "Hello?")

    sent = llm.calls[1]["history"]
    calls = {c["id"] for m in sent if m["role"] == "assistant" for c in m["tool_calls"]}
    outputs = {m["call_id"]: m for m in sent if m["role"] == "tool"}
    assert calls == set(outputs) == {"call_1"}
    assert outputs["call_1"]["content"] == INTERRUPTED
    assert outputs["call_1"]["is_error"] is True
    # The stored history isn't rewritten: the repair is made for each request.
    assert not any(m["role"] == "tool" for m in session.messages)


async def test_an_unavailable_llm_ends_the_turn_with_an_error() -> None:
    harness = Harness(DownLLM())

    events = await harness.turn(Session(id="s"), "Hello")

    assert events == [{"type": "error", "message": UNAVAILABLE}]


# Onboarding


@pytest.mark.parametrize(
    "tool",
    [
        call("list_reservations", date="2026-10-31"),
        call("block_slot", starts_at="2026-10-31T21:00:00+01:00"),
        call("cancel_reservation", reservation_id="R-001"),
    ],
)
async def test_tools_are_refused_before_onboarding(tool: ToolCall) -> None:
    agent = FakeAgent()
    harness = Harness(ScriptedLLM(([], [tool]), (["Let's set you up first."], [])), agent=agent)
    session = Session(id="s")

    events = await harness.turn(session, "Hi")

    assert session.messages[2] == {
        "role": "tool",
        "call_id": tool.id,
        "content": NOT_ONBOARDED,
        "is_error": True,
    }
    assert "reservations" not in kinds(events)
    assert agent.blocked == agent.cancelled == []


async def test_build_agent_streams_steps_shows_the_profile_and_switches_mode() -> None:
    llm = ScriptedLLM(
        ([], [call("build_agent", website="http://localhost:8080/", description="12 tables")]),
        (["Here is your agent. I assumed bookings last 2 hours: right?"], []),
    )
    harness = Harness(llm)
    session = Session(id="s")

    events = await harness.turn(session, "12 tables, groups up to 6")

    assert kinds(events) == ["tool_start", *["step"] * 10, "profile", "text"]
    assert session.merchant_slug == SLUG
    assert "Mode: onboarding" in llm.calls[0]["instructions"]
    assert "Mode: operations" in llm.calls[1]["instructions"]
    assert '"slug": "le-petit-zinc"' in llm.calls[1]["instructions"]
    # The model gets the assumptions to list them, not the generated tools.
    result = json.loads(session.messages[2]["content"])
    assert result["assumptions"] == ["Bookings last 120 minutes (default)."]
    assert "tools" not in result


async def test_build_agent_is_refused_once_onboarded() -> None:
    llm = ScriptedLLM(
        ([], [call("build_agent", website="http://localhost:8080/", description="x")]),
        (["Your agent already exists."], []),
    )
    harness = Harness(llm)
    harness.provisioned.add(SLUG)
    session = onboarded()

    await harness.turn(session, "Build it again")

    assert session.messages[2]["is_error"] is True


async def test_the_default_merchant_skips_onboarding() -> None:
    llm = ScriptedLLM((["Hello again."], []))
    harness = Harness(llm)
    harness.provisioned.add(SLUG)
    harness.assistant._default_merchant = SLUG
    session = Session(id="s")

    await harness.turn(session, "Hi")

    assert session.merchant_slug == SLUG
    assert "Mode: operations" in llm.calls[0]["instructions"]


@pytest.mark.parametrize(("owned", "attached"), [(None, None), (SLUG, SLUG)])
async def test_onboarding_an_existing_site_only_reaches_the_platform_merchant(
    owned: str | None, attached: str | None
) -> None:
    async def known_site(arguments: BuildAgentInput, emit: Any) -> BuildAgentOutput:
        raise AlreadyProvisioned(SLUG, "http://localhost:8080/")

    tools = AdminTools(FakeAgent(), known_site, TZ, today=lambda: TODAY, owned_merchant=owned)  # type: ignore[arg-type]
    session = Session(id="s")
    events: list[ChatEvent] = []

    async def emit(event: ChatEvent) -> None:
        events.append(event)

    result = await tools.run(
        session, call("build_agent", website="http://localhost:8080/", description="x"), emit
    )

    assert result.is_error is True
    assert session.merchant_slug == attached
    if attached is None:
        assert result.content == f"invalid_input: {SITE_TAKEN}"
        assert SLUG not in result.content


# Operations scenarios (AGENTS.md §9)


async def test_operations_cards() -> None:
    nine_pm = "2026-10-31T21:00:00+01:00"
    llm = ScriptedLLM(
        ([], [call("block_slot", "a", starts_at=nine_pm)]),
        ([], [call("update_policy", "b", max_party_size=6, services=None)]),
        ([], [call("get_stats", "c", date="2026-10-31")]),
        ([], [call("update_tables", "d", tables=[{"label": "T1", "seats": 4}])]),
        ([], [call("cancel_reservation", "e", reservation_id="R-001")]),
        (["Done."], []),
    )
    agent = FakeAgent()
    harness = Harness(llm, agent=agent)
    harness.provisioned.add(SLUG)

    events = await harness.turn(onboarded(), "Do it all")

    cards = [event for event in events if event["type"] == "card"]
    assert [card["kind"] for card in cards] == [
        "confirmation",
        "policy",
        "stats",
        "confirmation",
        "confirmation",
    ]
    assert cards[0]["data"]["text"] == "21:00 tonight blocked: no new bookings."
    assert cards[1]["data"]["max_party_size"] == 6
    assert cards[2]["data"]["date"] == "2026-10-31"
    assert cards[3]["data"]["text"] == "Floor plan updated: 1 tables · 4 seats."
    assert cards[4]["data"]["text"] == "R-001 cancelled. The guest will be notified."
    assert agent.blocked == [datetime.fromisoformat(nine_pm)]
    assert agent.cancelled == ["R-001"]


@pytest.mark.parametrize(
    ("reservation_id", "text"),
    [
        ("R-001", "R-001 cancelled. The guest will be notified."),
        ("R-002", "R-002 is in the past: cancelled, no notice sent."),
        ("R-003", "R-003 was already cancelled by the guest."),
        ("R-004", "R-004 was already cancelled: no new notice sent."),
    ],
)
async def test_a_merchant_cancellation_card_says_what_was_done(
    reservation_id: str, text: str
) -> None:
    llm = ScriptedLLM(
        ([], [call("cancel_reservation", reservation_id=reservation_id)]), (["Done."], [])
    )
    agent = FakeAgent()
    harness = Harness(llm, agent=agent)
    harness.provisioned.add(SLUG)
    session = onboarded()

    events = await harness.turn(session, f"Cancel {reservation_id}")

    cards = [event for event in events if event["type"] == "card"]
    assert [card["data"]["text"] for card in cards] == [text]
    assert agent.cancelled == [reservation_id]
    tool_message = next(m for m in session.messages if m["role"] == "tool")
    assert json.loads(tool_message["content"])["text"] == text  # what the model relays


def admin_tool_lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [
        {key: record.__dict__[key] for key in ("tool", "merchant", "outcome", "latency_ms")}
        for record in caplog.records
        if record.getMessage() == "admin_tool"
    ]


async def test_each_admin_tool_call_logs_one_line_without_guest_names(
    caplog: pytest.LogCaptureFixture,
) -> None:
    llm = ScriptedLLM(
        ([], [call("list_reservations", "a", date="2026-10-31")]),
        ([], [call("cancel_reservation", "b", reservation_id="R-001")]),
        ([], [call("cancel_reservation", "c", reservation_id="R-404")]),
        ([], [call("block_slot", "d", starts_at="tonight")]),
        ([], [call("drop_tables", "e")]),
        (["Done."], []),
    )
    harness = Harness(llm, agent=FakeAgent())
    harness.provisioned.add(SLUG)

    with caplog.at_level(logging.INFO, logger="merchant_platform.tools"):
        await harness.turn(onboarded(), "Tonight's bookings, then cancel")

    logged = admin_tool_lines(caplog)
    assert [(line["tool"], line["merchant"], line["outcome"]) for line in logged] == [
        ("list_reservations", SLUG, "ok"),
        ("cancel_reservation", SLUG, "ok"),
        ("cancel_reservation", SLUG, "not_found"),
        ("block_slot", SLUG, "invalid_input"),
        ("unknown", SLUG, "invalid_input"),
    ]
    assert all(isinstance(line["latency_ms"], int) for line in logged)
    assert "Alex Martin" not in caplog.text
    assert not any("Alex Martin" in str(record.__dict__) for record in caplog.records)


async def test_build_agent_logs_the_new_merchant(caplog: pytest.LogCaptureFixture) -> None:
    llm = ScriptedLLM(
        ([], [call("build_agent", website="https://lepetitzinc.example", description="Bistro")]),
        (["Your agent is live."], []),
    )

    with caplog.at_level(logging.INFO, logger="merchant_platform.tools"):
        await Harness(llm).turn(Session(id="s"), "Here is my site")

    assert [
        (line["tool"], line["merchant"], line["outcome"]) for line in admin_tool_lines(caplog)
    ] == [("build_agent", SLUG, "ok")]


async def test_a_tool_cancelled_mid_call_still_logs_its_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    started = asyncio.Event()

    async def slow_build(arguments: BuildAgentInput, emit: Any) -> BuildAgentOutput:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    tools = AdminTools(None, slow_build, TZ, today=lambda: TODAY)

    async def ignore(event: ChatEvent) -> None:
        pass

    build = call("build_agent", website="https://lepetitzinc.example", description="Bistro")
    with caplog.at_level(logging.INFO, logger="merchant_platform.tools"):
        task = asyncio.create_task(tools.run(Session(id="s"), build, ignore))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert [(line["tool"], line["outcome"]) for line in admin_tool_lines(caplog)] == [
        ("build_agent", "cancelled")
    ]


async def test_merchant_tools_without_an_agent_service_explain_it() -> None:
    llm = ScriptedLLM(([], [call("get_stats", date="2026-10-31")]), (["Not yet."], []))
    harness = Harness(llm, agent=None)
    harness.provisioned.add(SLUG)
    session = onboarded()

    await harness.turn(session, "How busy are we?")

    assert session.messages[2]["content"] == f"agent_unavailable: {NOT_AVAILABLE}"


# Prompt and history


def test_the_prompt_carries_the_date_time_zone_and_mode() -> None:
    onboarding = system_prompt(NOW, None)
    operations = system_prompt(NOW, PROFILE)

    assert "Today is Saturday 2026-10-31, it is 18:30 (Europe/Zurich, UTC+01:00)." in onboarding
    assert "Mode: onboarding" in onboarding
    assert "Mode: operations" in operations
    assert PROFILE.name in operations


@pytest.mark.parametrize(
    ("now", "today", "change"),
    [
        (NOW, "UTC+01:00", "The offset becomes UTC+02:00 on Sunday 2027-03-28"),
        (
            datetime(2026, 7, 1, 12, 0, tzinfo=TZ),
            "UTC+02:00",
            "The offset becomes UTC+01:00 on Sunday 2026-10-25",
        ),
        (
            datetime(2026, 10, 24, 23, 0, tzinfo=TZ),
            "UTC+02:00",
            "The offset becomes UTC+01:00 on Sunday 2026-10-25",
        ),
        (
            datetime(2026, 10, 25, 1, 30, tzinfo=TZ),
            "UTC+02:00",
            "The offset becomes UTC+01:00 on Sunday 2026-10-25",
        ),
        (
            datetime(2026, 10, 25, 18, 0, tzinfo=TZ),
            "UTC+01:00",
            "The offset becomes UTC+02:00 on Sunday 2027-03-28",
        ),
    ],
)
def test_the_prompt_gives_the_utc_offset_and_its_next_change(
    now: datetime, today: str, change: str
) -> None:
    """A model guessing the offset blocks the wrong hour after a clock change (#83)."""
    prompt = system_prompt(now, PROFILE)

    assert f"(Europe/Zurich, {today})." in prompt
    assert change in prompt


def test_the_prompt_mentions_no_offset_change_in_a_zone_without_one() -> None:
    prompt = system_prompt(datetime(2026, 10, 31, 18, 30, tzinfo=ZoneInfo("UTC")), None)

    assert "(UTC, UTC+00:00)." in prompt
    assert "The offset becomes" not in prompt


def test_compact_cuts_long_tool_results_from_old_turns_only() -> None:
    long = "x" * 1000
    history: list[Message] = []
    for turn in range(4):
        history += [
            {"role": "user", "content": f"turn {turn}"},
            {"role": "tool", "call_id": str(turn), "content": long, "is_error": False},
        ]

    compacted = compact(history)

    assert [m["content"] for m in compacted if m["role"] == "tool"] == [
        "(older result omitted)",
        long,
        long,
        long,
    ]
    assert history[1]["content"] == long  # the stored history is untouched


# The owner's secret link after a build (#132)

LINK = IssuedLink("https://sesame.example/api/login?token=s3cr3t", "fp-1")


async def run_build(issue_link: Any) -> tuple[Session, Any, list[ChatEvent]]:
    tools = AdminTools(None, fake_build, TZ, today=lambda: TODAY, issue_link=issue_link)
    session = Session(id="s")
    events: list[ChatEvent] = []

    async def emit(event: ChatEvent) -> None:
        events.append(event)

    call_ = call("build_agent", website="http://localhost:8080/", description="Bistro.")
    result = await tools.run(session, call_, emit)
    return session, result, events


async def test_a_build_shows_the_link_on_the_card_only() -> None:
    async def issue_link(slug: str) -> IssuedLink:
        return LINK

    session, result, events = await run_build(issue_link)

    cards = [e for e in events if isinstance(e, ProfileCard)]
    assert [card.login_url for card in cards] == [LINK.url]
    assert "s3cr3t" not in result.content  # the model reads the tool result: never the link
    assert (session.merchant_slug, session.link) == (SLUG, "fp-1")
    assert "s3cr3t" not in json.dumps(session.messages)


async def test_a_link_that_cant_be_stored_leaves_the_session_unattached() -> None:
    async def failing(slug: str) -> IssuedLink:
        raise RuntimeError("database down")

    session, result, events = await run_build(failing)

    assert result.is_error
    assert (session.merchant_slug, session.link) == (None, None)
    assert not [e for e in events if isinstance(e, ProfileCard)]


def test_block_slot_confirmation_names_the_day() -> None:
    tools = AdminTools(None, fake_build, TZ, today=lambda: TODAY)
    tomorrow_noon = datetime.combine(TODAY + timedelta(days=1), datetime.min.time(), TZ).replace(
        hour=12
    )

    assert tools._when(tomorrow_noon) == "12:00 on Sun 1 Nov"
    assert tools._when(NOW.replace(hour=12, minute=0)) == "12:00 today"


# Mini-evaluation against the real API (`llm` marker): rerun after every prompt change.


class _LlmEnv(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-6.1-terra"
    openai_reasoning_effort: str | None = "low"


def real_llm() -> OpenAIClient:
    env = _LlmEnv()
    assert env.openai_api_key is not None
    client = AsyncOpenAI(api_key=env.openai_api_key.get_secret_value(), max_retries=0)
    return OpenAIClient(client, env.openai_model, reasoning_effort=env.openai_reasoning_effort)


def tools_called(session: Session, since: int = 0) -> list[tuple[str, dict[str, Any]]]:
    return [
        (call["name"], json.loads(call["arguments"]))
        for message in session.messages[since:]
        if message["role"] == "assistant"
        for call in message["tool_calls"]
    ]


async def live_turns(*messages: str, onboarded_already: bool = True) -> tuple[Harness, Session]:
    harness = Harness(real_llm(), agent=FakeAgent())
    session = onboarded() if onboarded_already else Session(id="s")
    if onboarded_already:
        harness.provisioned.add(SLUG)
    for message in messages:
        await harness.turn(session, message)
    return harness, session


@pytest.mark.llm
async def test_live_tonights_bookings() -> None:
    _, session = await live_turns("What bookings do I have tonight?")
    assert tools_called(session) == [("list_reservations", {"date": "2026-10-31"})]


@pytest.mark.llm
async def test_live_block_a_slot() -> None:
    _, session = await live_turns("Block 21:00 tonight")
    [(name, arguments)] = tools_called(session)
    assert name == "block_slot"
    starts_at = datetime.fromisoformat(arguments["starts_at"]).astimezone(TZ)
    assert (starts_at.date(), starts_at.hour, starts_at.minute) == (TODAY, 21, 0)


@pytest.mark.llm
async def test_live_max_party_size() -> None:
    _, session = await live_turns("Set max party size to 6")
    [(name, arguments)] = tools_called(session)
    assert (name, arguments["max_party_size"]) == ("update_policy", 6)


@pytest.mark.llm
async def test_live_how_busy() -> None:
    _, session = await live_turns("How busy are we tonight?")
    calls = tools_called(session)
    # Facts, not one exact path: tonight's stats, and only reads (the model may also list the
    # bookings, #109).
    assert ("get_stats", {"date": "2026-10-31"}) in calls
    assert {name for name, _ in calls} <= {"get_stats", "list_reservations"}


@pytest.mark.llm
async def test_live_cancel_asks_for_confirmation_first() -> None:
    harness, session = await live_turns("Cancel R-001")
    assert tools_called(session) == []
    asked = len(session.messages)
    await harness.turn(session, "Yes, cancel it")
    assert tools_called(session, asked) == [("cancel_reservation", {"reservation_id": "R-001"})]


@pytest.mark.llm
async def test_live_onboarding_asks_then_builds() -> None:
    harness, session = await live_turns("Hi!", onboarded_already=False)
    assert tools_called(session) == []
    await harness.turn(session, "http://localhost:8080/")
    assert tools_called(session) == []
    await harness.turn(
        session,
        "Lunch 12-14 and dinner 19-22 every day, 12 tables (four of 2, six of 4, two of 6), "
        "groups up to 6 online, vegetarian and gluten-free options, a terrace, children "
        "welcome, free cancellation until 2 hours before.",
    )
    [(name, arguments)] = tools_called(session)
    assert name == "build_agent"
    assert arguments["website"].startswith("http://localhost:8080")
    assert session.merchant_slug == SLUG


def test_a_model_call_that_stops_sending_gives_up_after_60_s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The web counts heartbeats as activity (#87): the server must bound a hung call itself.
    built: list[dict[str, Any]] = []

    def capture(**kwargs: Any) -> AsyncOpenAI:
        built.append(kwargs)
        return AsyncOpenAI(**kwargs)

    monkeypatch.setattr("merchant_platform.assistant.AsyncOpenAI", capture)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url="postgresql+psycopg://unused/db",
        openai_api_key=SecretStr("sk-test"),
    )

    default_assistant(settings, cast(Any, None))

    [kwargs] = built
    assert kwargs["timeout"].read == 60
    assert kwargs["timeout"].connect == 5
    assert kwargs["max_retries"] == 0


def test_every_cancellation_outcome_has_its_wording() -> None:
    # A new outcome (own_booking, #129) must never reach the card as a KeyError.
    assert set(CANCELLED) == set(get_args(MerchantCancelOutcome))
