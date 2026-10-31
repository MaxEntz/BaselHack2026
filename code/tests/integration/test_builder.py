"""build_agent on Postgres with a fake crawl and LLM: steps, slug, cache, chat onboarding."""

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.provisioning import SlugTaken, deprovision
from builder.build import GENERATED_DIR, AlreadyProvisioned, Builder
from builder.cache import BuildCache
from builder.crawl import CrawlResult, Page, StepEmitter
from builder.extract import TOOL_NAME, ExtractionFailed
from builder.llm import LLMReply, LLMUnavailable, Message, OnText, ToolCall, ToolSpec
from core.config import Settings
from core.db import merchants_t, schema_for
from core.errors import AgentUnavailable
from core.models import BuildAgentInput, BuildAgentOutput, StepEvent
from merchant_platform.api import create_app
from merchant_platform.assistant import Assistant, registry_profiles
from merchant_platform.tools import AdminTools

pytestmark = pytest.mark.integration

FIXTURE = json.loads((Path(__file__).parents[1] / "fixtures" / "le-petit-zinc.json").read_text())
MCP_URL = "https://gateway.example/mcp"
STEPS = ("crawl", "extract", "validate", "provision", "deploy")


def draft(name: str, **overrides: Any) -> dict[str, Any]:
    fields = {
        key: FIXTURE[key]
        for key in (
            "location",
            "seats",
            "services",
            "max_party_size",
            "tables",
            "tags",
            "price_level",
            "cancellation_policy",
        )
    }
    return fields | {"name": name, "fallback_url": None, "assumptions": []} | overrides


class FakeLLM:
    def __init__(self, *inputs: dict[str, Any] | None, chat_site: str = "") -> None:
        self.inputs = list(inputs)
        self.chat_site = chat_site
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
        if tool_choice is None:
            # The platform assistant: build the agent, then comment once it is done.
            if self.calls == 1:
                arguments = json.dumps({"website": self.chat_site, "description": "12 tables"})
                return LLMReply(text="", tool_calls=(ToolCall("c1", "build_agent", arguments),))
            await on_text("Your agent is live.")
            return LLMReply(text="Your agent is live.")
        arguments = self.inputs.pop(0)
        if arguments is None:
            raise LLMUnavailable("InternalServerError")
        return LLMReply(text="", tool_calls=(ToolCall("x", TOOL_NAME, json.dumps(arguments)),))


class FakeCrawl:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, url: str, on_event: StepEmitter) -> CrawlResult:
        self.calls += 1
        await on_event(StepEvent(step="crawl", status="running", detail="Loading homepage"))
        result = CrawlResult(
            website=url,
            pages=[
                Page(url=url, title="Home", text="Bistrot à Mulhouse.", json_ld=[]),
                Page(url=f"{url}reservation.html", title="Réserver", text="", json_ld=[]),
            ],
        )
        await on_event(StepEvent(step="crawl", status="done", detail="2 pages read", duration_ms=5))
        return result


class Run:
    def __init__(self) -> None:
        self.events: list[StepEvent] = []

    async def emit(self, event: StepEvent) -> None:
        self.events.append(event)


@pytest.fixture
async def slugs(engine: AsyncEngine) -> AsyncIterator[list[str]]:
    """Merchants created by the test, deprovisioned afterwards."""
    created: list[str] = []
    yield created
    for slug in created:
        await deprovision(slug, engine=engine)


def unique_site() -> tuple[str, str]:
    token = uuid4().hex[:8]
    return f"http://zinc-{token}.example/", f"Zinc {token}"


def builder(
    engine: AsyncEngine, llm: FakeLLM, tmp_path: Path, crawl: FakeCrawl | None = None, **kw: Any
) -> Builder:
    return Builder(
        llm,
        engine,
        model="gpt-test",
        mcp_url=MCP_URL,
        cache=kw.pop("cache", BuildCache("off")),
        crawl=crawl or FakeCrawl(),
        generated_dir=tmp_path / "generated",
    )


async def status_of(engine: AsyncEngine, slug: str) -> str | None:
    async with engine.connect() as conn:
        return await conn.scalar(select(merchants_t.c.status).where(merchants_t.c.slug == slug))


async def test_build_agent_runs_the_five_steps_and_publishes_the_agent(
    engine: AsyncEngine, tmp_path: Path, slugs: list[str]
) -> None:
    site, name = unique_site()
    run = Run()

    output = await builder(engine, FakeLLM(draft(name)), tmp_path).build_agent(
        site, "12 tables", run.emit
    )
    slugs.append(output.profile.slug)

    assert [(e.step, e.status) for e in run.events] == [
        (step, status) for step in STEPS for status in ("running", "done")
    ]
    assert all(e.duration_ms is not None for e in run.events if e.status == "done")
    assert output.status == "live"
    assert output.mcp_url == MCP_URL
    assert str(output.profile.website) == site
    assert str(output.profile.fallback_url) == f"{site}reservation.html"
    assert {tool.name for tool in output.tools} >= {"get_availability", "create_reservation"}
    assert await status_of(engine, output.profile.slug) == "live"
    saved = tmp_path / "generated" / f"{output.profile.slug}.json"
    assert json.loads(saved.read_text())["slug"] == output.profile.slug


async def test_a_taken_slug_gets_a_suffix_and_a_known_site_is_refused(
    engine: AsyncEngine, tmp_path: Path, slugs: list[str]
) -> None:
    site, name = unique_site()
    other_site, _ = unique_site()
    llm = FakeLLM(draft(name), draft(name))
    build = builder(engine, llm, tmp_path)

    first = await build.build_agent(site, "", Run().emit)
    slugs.append(first.profile.slug)
    second = await build.build_agent(other_site, "", Run().emit)
    slugs.append(second.profile.slug)
    run = Run()
    with pytest.raises(AlreadyProvisioned) as known:
        await build.build_agent(site.rstrip("/"), "", run.emit)

    assert second.profile.slug == f"{first.profile.slug}-2"
    assert known.value.slug == first.profile.slug
    assert run.events == []


async def test_an_extraction_that_fails_twice_provisions_nothing(
    engine: AsyncEngine, tmp_path: Path, slugs: list[str]
) -> None:
    site, name = unique_site()
    no_hours = draft(name, services=[])
    run = Run()

    with pytest.raises(ExtractionFailed):
        await builder(engine, FakeLLM(no_hours, no_hours), tmp_path).build_agent(site, "", run.emit)

    assert [(e.step, e.status) for e in run.events][-1] == ("validate", "error")
    assert "service hours" in (run.events[-1].detail or "")
    async with engine.connect() as conn:
        found = await conn.scalar(select(merchants_t.c.slug).where(merchants_t.c.website == site))
    assert found is None


async def test_an_unavailable_llm_fails_the_extract_step(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    site, _ = unique_site()
    run = Run()

    with pytest.raises(AgentUnavailable):
        await builder(engine, FakeLLM(None), tmp_path).build_agent(site, "", run.emit)

    assert [(e.step, e.status) for e in run.events][-1] == ("extract", "error")


async def test_a_warm_cache_replays_the_steps_without_crawl_or_llm(
    engine: AsyncEngine, tmp_path: Path, slugs: list[str]
) -> None:
    site, name = unique_site()
    cache = BuildCache("read_write", tmp_path / "cache")
    crawl, llm = FakeCrawl(), FakeLLM(draft(name))
    first = await builder(engine, llm, tmp_path, crawl, cache=cache).build_agent(
        site, "12 tables", Run().emit
    )
    await deprovision(first.profile.slug, engine=engine)

    run = Run()
    again = await builder(engine, llm, tmp_path, crawl, cache=cache).build_agent(
        site, "12 tables", run.emit
    )
    slugs.append(again.profile.slug)

    assert (crawl.calls, llm.calls) == (1, 1)
    assert [(e.step, e.status) for e in run.events] == [
        (step, status) for step in STEPS for status in ("running", "done")
    ]
    assert run.events[1].detail == "2 pages read: homepage, reservation"
    assert again.profile == first.profile


@pytest.mark.parametrize("mode", ["read_only", "off"])
async def test_cache_modes_that_store_nothing(
    engine: AsyncEngine, tmp_path: Path, slugs: list[str], mode: str
) -> None:
    site, name = unique_site()
    cache = BuildCache(mode, tmp_path / "cache")  # type: ignore[arg-type]
    crawl, llm = FakeCrawl(), FakeLLM(draft(name), draft(name))

    first = await builder(engine, llm, tmp_path, crawl, cache=cache).build_agent(
        site, "", Run().emit
    )
    await deprovision(first.profile.slug, engine=engine)
    again = await builder(engine, llm, tmp_path, crawl, cache=cache).build_agent(
        site, "", Run().emit
    )
    slugs.append(again.profile.slug)

    assert (crawl.calls, llm.calls) == (2, 2)
    assert not (tmp_path / "cache").exists()


async def test_onboarding_through_the_chat_api(
    engine: AsyncEngine, tmp_path: Path, slugs: list[str]
) -> None:
    site, name = unique_site()
    tz = ZoneInfo("Europe/Zurich")
    llm = FakeLLM(draft(name), chat_site=site)
    build_ = builder(engine, llm, tmp_path)

    async def build(arguments: BuildAgentInput, emit: StepEmitter) -> BuildAgentOutput:
        return await build_.build_agent(str(arguments.website), arguments.description, emit)

    tools = AdminTools(None, build, tz, today=date.today)
    app = create_app(
        Settings(_env_file=None, database_url="postgresql+psycopg://unused/db"),  # type: ignore[call-arg]
        assistant=Assistant(llm, tools, registry_profiles(engine), tz),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.post("/chat", json={"session_id": "s", "message": site})

    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    slug = events[11]["data"]["profile"]["slug"]
    slugs.append(slug)
    assert [e.get("kind", e["type"]) for e in events] == [
        "tool_start",
        *["step"] * 10,
        "profile",
        "text",
        "done",
    ]
    assert app.state.sessions.get("s").merchant_slug == slug


@pytest.mark.parametrize("stop", [RuntimeError("deploy failed"), asyncio.CancelledError()])
async def test_a_build_stopped_before_deploy_leaves_nothing_behind(
    engine: AsyncEngine,
    tmp_path: Path,
    slugs: list[str],
    monkeypatch: pytest.MonkeyPatch,
    stop: BaseException,
) -> None:
    site, name = unique_site()

    async def failing_deploy(slug: str, *, engine: AsyncEngine | None = None) -> None:
        raise stop

    monkeypatch.setattr("builder.build.deploy", failing_deploy)
    run = Run()
    with pytest.raises(type(stop)):
        await builder(engine, FakeLLM(draft(name)), tmp_path).build_agent(site, "", run.emit)

    async with engine.connect() as conn:
        left = await conn.scalar(select(merchants_t.c.slug).where(merchants_t.c.website == site))
    assert left is None
    monkeypatch.undo()
    # The site can be onboarded again straight away.
    output = await builder(engine, FakeLLM(draft(name)), tmp_path).build_agent(site, "", Run().emit)
    slugs.append(output.profile.slug)
    assert await status_of(engine, output.profile.slug) == "live"


async def test_a_building_leftover_does_not_block_its_site(
    engine: AsyncEngine, tmp_path: Path, slugs: list[str]
) -> None:
    site, name = unique_site()
    leftover = await builder(engine, FakeLLM(draft(name)), tmp_path).build_agent(
        site, "", Run().emit
    )
    slugs.append(leftover.profile.slug)
    async with engine.begin() as conn:
        await conn.execute(
            merchants_t.update()
            .where(merchants_t.c.slug == leftover.profile.slug)
            .values(status="building")
        )

    output = await builder(engine, FakeLLM(draft(name)), tmp_path).build_agent(site, "", Run().emit)
    slugs.append(output.profile.slug)

    assert output.profile.slug == f"{leftover.profile.slug}-2"


def test_generated_profiles_stay_out_of_the_source_tree() -> None:
    assert GENERATED_DIR.parts[-3:] == (".cache", "builder", "generated")


async def test_a_build_that_loses_the_slug_race_leaves_the_winner_alone(
    engine: AsyncEngine, tmp_path: Path, slugs: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    site, name = unique_site()
    winner = await builder(engine, FakeLLM(draft(name)), tmp_path).build_agent(site, "", Run().emit)
    slugs.append(winner.profile.slug)

    # A concurrent build got the slug between `_unique_slug` and `provision`.
    async def same_slug(self: Builder, base: str) -> str:
        return base

    monkeypatch.setattr(Builder, "_unique_slug", same_slug)
    other_site, _ = unique_site()
    run = Run()
    with pytest.raises(SlugTaken):
        await builder(engine, FakeLLM(draft(name)), tmp_path).build_agent(other_site, "", run.emit)

    assert [(e.step, e.status) for e in run.events][-1] == ("provision", "error")
    assert await status_of(engine, winner.profile.slug) == "live"
    async with engine.connect() as conn:
        schema = await conn.scalar(
            text("SELECT count(*) FROM pg_namespace WHERE nspname = :name"),
            {"name": schema_for(winner.profile.slug)},
        )
    assert schema == 1
