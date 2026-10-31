"""Platform assistant tool-use loop with a scripted LLM, a fake agent service and builder."""

import json
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from openai import AsyncOpenAI
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from agent.service import AgentService
from core.errors import NotFound
from core.models import (
    AdminReservationRow,
    BuildAgentInput,
    BuildAgentOutput,
    ChatEvent,
    GeneratedTool,
    MerchantProfile,
    Policy,
    Reservation,
    Service,
    Stats,
    StepEvent,
    TableSpec,
)
from merchant_platform.assistant import (
    MAX_ITERATIONS,
    TOO_MANY_STEPS,
    UNAVAILABLE,
    Assistant,
    compact,
    system_prompt,
)
from merchant_platform.llm import (
    LLMClient,
    LLMReply,
    LLMUnavailable,
    Message,
    OnText,
    OpenAIClient,
    ToolCall,
    ToolSpec,
)
from merchant_platform.sessions import Session
from merchant_platform.tools import NOT_AVAILABLE, NOT_ONBOARDED, AdminTools

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

    async def cancel_reservation(
        self, slug: str, reservation_id: str, *, by: str, guest_name: str | None = None
    ) -> Reservation:
        if reservation_id != "R-001":
            raise NotFound(f"No booking {reservation_id}")
        self.cancelled.append(reservation_id)
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

    assert "Today is Saturday 2026-10-31, it is 18:30 (Europe/Zurich)." in onboarding
    assert "Mode: onboarding" in onboarding
    assert "Mode: operations" in operations
    assert PROFILE.name in operations


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
    assert tools_called(session) == [("get_stats", {"date": "2026-10-31"})]


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
