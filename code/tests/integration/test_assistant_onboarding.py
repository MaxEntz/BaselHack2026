"""Onboarding end to end on Postgres: /chat → assistant → build_agent stub → provisioned agent.

The LLM is scripted; the database, provisioning and the API are real.
"""

import json
from collections.abc import AsyncIterator, Sequence
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.provisioning import deprovision
from core.config import Settings
from core.db import merchants_t
from core.models import BuildAgentInput, StepEvent
from merchant_platform.api import create_app
from merchant_platform.assistant import Assistant, registry_profiles
from merchant_platform.build_stub import fixture_build_agent
from merchant_platform.llm import LLMReply, Message, OnText, ToolCall, ToolSpec
from merchant_platform.tools import AdminTools

pytestmark = pytest.mark.integration

TZ = ZoneInfo("Europe/Zurich")
SLUG = "le-petit-zinc"
MCP_URL = "https://gateway.example/mcp"


@pytest.fixture
async def clean(engine: AsyncEngine) -> AsyncIterator[None]:
    await deprovision(SLUG, engine=engine)
    yield
    await deprovision(SLUG, engine=engine)


async def test_the_stub_provisions_the_fixture_and_keeps_it_on_a_rerun(
    engine: AsyncEngine, clean: None
) -> None:
    build = fixture_build_agent(engine, MCP_URL, pause_s=0)
    steps: list[StepEvent] = []

    async def emit(step: StepEvent) -> None:
        steps.append(step)

    arguments = BuildAgentInput(website="http://localhost:8080/", description="12 tables")
    output = await build(arguments, emit)
    again = await build(arguments, emit)

    assert [(s.step, s.status) for s in steps[:10]] == [
        (name, status)
        for name in ("crawl", "extract", "validate", "provision", "deploy")
        for status in ("running", "done")
    ]
    assert steps[7].detail == "Created 12 tables with 44 seats."
    assert steps[17].detail == "Your agent already existed: kept as is."
    assert output.profile.slug == again.profile.slug == SLUG
    assert output.mcp_url == MCP_URL
    assert {tool.name for tool in output.tools} >= {"get_availability", "create_reservation"}
    async with engine.connect() as conn:
        status = await conn.scalar(select(merchants_t.c.status).where(merchants_t.c.slug == SLUG))
    assert status == "live"
    assert await registry_profiles(engine)(SLUG) == output.profile
    assert await registry_profiles(engine)("chez-personne") is None


class ScriptedLLM:
    def __init__(self, *replies: tuple[str, list[ToolCall]]) -> None:
        self.replies = list(replies)
        self.instructions: list[str] = []

    async def respond(
        self,
        *,
        instructions: str,
        tools: Sequence[ToolSpec],
        history: Sequence[Message],
        on_text: OnText,
    ) -> LLMReply:
        self.instructions.append(instructions)
        text, calls = self.replies.pop(0)
        if text:
            await on_text(text)
        return LLMReply(text=text, tool_calls=tuple(calls))


async def test_onboarding_through_the_chat_api(engine: AsyncEngine, clean: None) -> None:
    arguments = {"website": "http://localhost:8080/", "description": "12 tables, groups up to 6"}
    llm = ScriptedLLM(
        ("", [ToolCall("c1", "build_agent", json.dumps(arguments))]),
        ("Your agent is live. I assumed bookings last 2 hours: is that right?", []),
    )
    build = fixture_build_agent(engine, MCP_URL, pause_s=0)
    tools = AdminTools(None, build, TZ, today=date.today)
    assistant = Assistant(llm, tools, registry_profiles(engine), TZ)
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://unused/db")  # type: ignore[call-arg]
    app = create_app(settings, assistant=assistant)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.post(
            "/chat", json={"session_id": "s", "message": "lepetitzinc.fr, 12 tables"}
        )

    events: list[dict[str, Any]] = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    kinds = [event.get("kind", event["type"]) for event in events]
    assert kinds == ["tool_start", *["step"] * 10, "profile", "text", "done"]
    assert events[11]["data"]["profile"]["slug"] == SLUG
    assert app.state.sessions.get("s").merchant_slug == SLUG
    assert "Mode: onboarding" in llm.instructions[0]
    assert "Mode: operations" in llm.instructions[1]
