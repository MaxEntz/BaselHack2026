"""The platform assistant's test doubles: a scripted model, a fake agent service and builder,
and a harness that runs turns through them (`test_assistant*.py`, `test_studio_tools.py`)."""

import json
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from agent.service import AgentService, MerchantCancellation
from core.errors import NotFound
from core.llm import (
    LLMClient,
    LLMReply,
    LLMUnavailable,
    Message,
    OnText,
    ToolCall,
    ToolSpec,
)
from core.models import (
    AdminReservationRow,
    BuildAgentInput,
    BuildAgentOutput,
    BuildFact,
    BuildFlag,
    ChatEvent,
    GeneratedTool,
    MerchantCancelOutcome,
    MerchantProfile,
    MerchantStatus,
    Policy,
    Reservation,
    Service,
    Stats,
    StepEvent,
    TableSpec,
)
from merchant_platform.assistant import (
    Assistant,
)
from merchant_platform.registry import MerchantRecord
from merchant_platform.sessions import Session
from merchant_platform.tools import AdminTools

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
    for step in ("crawl", "extract", "validate", "provision"):
        await emit(StepEvent(step=step, status="running"))  # type: ignore[arg-type]
        await emit(StepEvent(step=step, status="done", duration_ms=1))  # type: ignore[arg-type]
    await emit(StepEvent(step="deploy", status="waiting", detail="Waiting for you."))
    return BuildAgentOutput(
        profile=PROFILE,
        status="building",
        assumptions=("Bookings last 120 minutes (default).",),
        facts=(
            BuildFact(
                key="max_party_size",
                label="Largest group",
                value='6, from "jusqu\'à 6 personnes"',
                source="/reservation.html",
                quote="jusqu'à 6 personnes",
            ),
        ),
        flags=(BuildFlag(title="No cancellation policy on your site.", detail="Guests see none."),),
        tools=(GeneratedTool(name="get_availability", description="Free tables."),),
        mcp_url="http://localhost:8001/mcp",
    )


def record_of(profile: MerchantProfile, status: MerchantStatus) -> MerchantRecord:
    """`profile`'s registry row, as `registry_records` reads it."""
    city = profile.location.city if profile.location else None
    return MerchantRecord(
        profile.slug,
        profile.name,
        city,
        profile.price_level,
        profile.tags,
        str(profile.website),
        status,
        profile.look,
        profile.model_dump(mode="json"),
    )


def call(name: str, call_id: str | None = None, **arguments: Any) -> ToolCall:
    return ToolCall(id=call_id or f"call-{name}", name=name, arguments=json.dumps(arguments))


class Harness:
    def __init__(
        self, llm: LLMClient, *, agent: FakeAgent | None = None, can_publish: bool = True
    ) -> None:
        self.llm = llm
        self.agent = agent
        self.events: list[ChatEvent] = []
        self.provisioned: set[str] = set()
        self.published: list[str] = []
        # The registry's status, which the Publish button and the pause switch also change.
        self.status: MerchantStatus = "building"
        tools = AdminTools(
            cast(AgentService, agent) if agent else None,
            self._build,
            TZ,
            today=lambda: TODAY,
            publish=self._publish if can_publish else None,
        )
        self.assistant = Assistant(llm, tools, self._merchants, TZ, clock=lambda: NOW)

    async def _build(self, arguments: BuildAgentInput, emit: Any) -> BuildAgentOutput:
        output = await fake_build(arguments, emit)
        self.provisioned.add(output.profile.slug)
        return output

    async def _publish(self, slug: str, emit: Any) -> None:
        await emit(StepEvent(step="deploy", status="running"))
        self.published.append(slug)
        self.status = "live"
        await emit(StepEvent(step="deploy", status="done", detail="Your agent is live."))

    async def _merchants(self, slug: str) -> MerchantRecord | None:
        return record_of(PROFILE, self.status) if slug in self.provisioned else None

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
