"""Publishing an agent a build left waiting (#136), through `POST /merchants/{slug}/publish`."""

import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from builder.crawl import CrawlResult, Page, StepEmitter
from builder.llm import LLMReply, Message, OnText, ToolCall, ToolSpec
from builder.look import read_look
from core.config import Settings
from core.db import merchants_t
from core.models import BuildAgentInput, BuildAgentOutput, ChatEvent, MerchantOverview
from merchant_platform.accounts import Accounts, MemoryOwnerStore, Signer
from merchant_platform.api import create_app
from merchant_platform.assistant import (
    STATUS_LINES,
    Assistant,
    default_assistant,
    registry_profiles,
    registry_statuses,
)
from merchant_platform.overview import registry_records
from merchant_platform.sessions import Session
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


async def test_a_look_changed_in_studio_is_what_the_overview_reads(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    # #152: update_look stores into the registry's profile copy; GET /merchants/{slug} reads it.
    from builder.look import DEFAULT_LOOK
    from merchant_platform.overview import look_store

    slug = await make_merchant()
    look = DEFAULT_LOOK.model_copy(update={"accent": "#2450c8", "corners": "square"})

    await look_store(engine)(slug, look)

    record = await registry_records(engine)(slug)
    assert record is not None
    assert record.look == look


# The homepage's computed styles, as the crawl reads them (#149).
SITE_STYLES = {
    "bg": "rgb(237, 235, 226)",
    "ink": "rgb(71, 82, 47)",
    "heading": {"size": 150, "color": "rgb(114, 127, 85)", "font": '"Fraunces Soft", serif'},
    "backgrounds": {"rgb(114, 127, 85)": 200_000.0, "rgb(248, 246, 239)": 130_000.0},
    "page_area": 1_000_000.0,
}


class ResetsTheLook:
    """A model that asks for the site's own look once, then says it's done."""

    def __init__(self) -> None:
        self.calls = 0

    async def respond(
        self,
        *,
        instructions: str,
        tools: Sequence[ToolSpec],
        history: Sequence[Message],
        on_text: OnText,
        tool_choice: str | None = None,
    ) -> LLMReply:
        self.calls += 1
        if self.calls == 1:
            reset = ToolCall("look", "update_look", json.dumps({"reset": True}))
            return LLMReply(text="", tool_calls=(reset,))
        await on_text("Done.")
        return LLMReply(text="Done.")


async def test_the_sites_look_is_read_again_through_the_injected_crawler(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    # #220: site_look called the module's crawl, so a fake crawler never covered this path.
    slug = await make_merchant()
    crawled: list[str] = []

    async def fake_crawl(url: str, on_event: StepEmitter) -> CrawlResult:
        crawled.append(url)
        page = Page(url=url, title="Le Petit Zinc", text="", json_ld=[], look=SITE_STYLES)
        return CrawlResult(website=url, pages=[page])

    assistant = default_assistant(settings(), engine, llm=ResetsTheLook(), crawler=fake_crawl)
    events: list[ChatEvent] = []

    async def emit(event: ChatEvent) -> None:
        events.append(event)

    await assistant(Session(id="s", merchant_slug=slug), "Back to my site's look", emit)

    record = await registry_records(engine)(slug)
    assert record is not None
    assert crawled == [record.website]
    assert record.look == read_look(SITE_STYLES)[0]
    assert not [event for event in events if event.type == "error"]
