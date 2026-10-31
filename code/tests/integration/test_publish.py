"""Publishing an agent a build left waiting (#136), through `POST /merchants/{slug}/publish`."""

from collections.abc import Awaitable, Callable, Sequence
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from builder.llm import LLMReply, Message, OnText, ToolSpec
from core.config import Settings
from core.db import merchants_t
from core.models import BuildAgentInput, BuildAgentOutput, MerchantOverview
from merchant_platform.accounts import Accounts, MemoryOwnerStore, Signer
from merchant_platform.api import create_app
from merchant_platform.assistant import (
    STATUS_LINES,
    Assistant,
    registry_profiles,
    registry_statuses,
)
from merchant_platform.overview import registry_records
from merchant_platform.tools import AdminTools

pytestmark = pytest.mark.integration

MerchantFactory = Callable[..., Awaitable[str]]
TZ = ZoneInfo("Europe/Zurich")


def settings() -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://unused/db")  # type: ignore[call-arg]


async def status_of(engine: AsyncEngine, slug: str) -> Any:
    async with engine.connect() as conn:
        return await conn.scalar(select(merchants_t.c.status).where(merchants_t.c.slug == slug))


async def test_the_owner_publishes_a_waiting_agent(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(live=False)
    owners = Accounts(MemoryOwnerStore(), Signer(b"k" * 32))
    token = await owners.issue_link_token(slug)
    app = create_app(settings(), accounts=owners, engine=engine, registry=registry_records(engine))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        stranger = await http.post(f"/merchants/{slug}/publish")
        await http.get("/login", params={"token": token})
        published = await http.post(f"/merchants/{slug}/publish")

    assert stranger.status_code == 401
    assert await status_of(engine, slug) == "live"
    overview = MerchantOverview.model_validate(published.json())
    assert overview.status == "live"
    assert overview.mcp_url.endswith(f"/m/{slug}/mcp")


class Recorder:
    """A model that answers in one sentence and keeps the prompts it was given."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def respond(
        self,
        *,
        instructions: str,
        tools: Sequence[ToolSpec],
        history: Sequence[Message],
        on_text: OnText,
        tool_choice: str | None = None,
    ) -> LLMReply:
        self.prompts.append(instructions)
        await on_text("Noted.")
        return LLMReply(text="Noted.")


async def no_build(arguments: BuildAgentInput, emit: Any) -> BuildAgentOutput:
    raise AssertionError("no build in this test")


async def test_the_assistant_hears_of_a_publish_through_the_route(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    """Studio's Publish button calls the route, outside the conversation (#198)."""
    slug = await make_merchant(live=False)
    owners = Accounts(MemoryOwnerStore(), Signer(b"k" * 32))
    token = await owners.issue_link_token(slug)
    llm = Recorder()
    assistant = Assistant(
        llm,
        AdminTools(None, no_build, TZ, today=date.today),
        registry_profiles(engine),
        TZ,
        statuses=registry_statuses(engine),
    )
    app = create_app(
        settings(),
        assistant=assistant,
        profiles=registry_profiles(engine),
        accounts=owners,
        engine=engine,
        registry=registry_records(engine),
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        await http.get("/login", params={"token": token})
        await http.post("/chat", json={"session_id": "s", "message": "What's left to do?"})
        published = await http.post(f"/merchants/{slug}/publish")
        await http.post("/chat", json={"session_id": "s", "message": "Is my agent live?"})

    assert published.status_code == 200
    before, after = llm.prompts
    assert STATUS_LINES["building"] in before
    assert STATUS_LINES["live"] in after
