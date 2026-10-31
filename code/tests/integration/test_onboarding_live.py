"""The onboarding conversation with a real model (#75): the platform assistant and the builder
on OpenAI, the real `Builder` and Postgres, a fake crawl (no Chromium, no network).

The merchant sends their URL, then their description, then "yes". The assistant must ask for
both before calling `build_agent`, build from that URL, show the profile card and read the
assumptions out. Run by hand after a prompt change: `uv run pytest -m llm` (needs
OPENAI_API_KEY and Docker).
"""

import json
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import Any, NamedTuple
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.provisioning import deprovision
from builder.build import Builder, publish
from builder.cache import BuildCache
from builder.crawl import CrawlResult, Page, StepEmitter
from builder.llm import OpenAIClient
from core.config import Settings
from core.models import BuildAgentInput, BuildAgentOutput, StepEvent
from merchant_platform.accounts import Accounts, IssuedLink, MemoryOwnerStore, Signer, issued
from merchant_platform.api import create_app
from merchant_platform.assistant import Assistant, registry_profiles, registry_statuses
from merchant_platform.tools import AdminTools

pytestmark = [pytest.mark.integration, pytest.mark.llm]

TZ = ZoneInfo("Europe/Zurich")
# The fictional brasserie of the extraction bench: CHF prices, kitchen hours, closed on Monday.
HOMEPAGE_TEXT = """\
Brasserie zum Rhein
Saisonale Basler Küche am Rheinufer, Rheingasse 1, 4058 Basel.
Speisekarte
Kalbsgeschnetzeltes mit Rösti — CHF 38.50
Eglifilet mit Salzkartoffeln — CHF 34.00
Hausgemachte Ravioli (vegetarisch) — CHF 29.50
Öffnungszeiten
Dienstag bis Sonntag 11.00 - 24.00 Uhr. Warme Küche 11.30 - 14.00 und 18.00 - 21.30 Uhr.
Reservation
Telefonisch unter 061 000 00 00. Gruppen ab 9 Personen bitte anrufen."""
DESCRIPTION = (
    "We're a brasserie in Basel, closed on Mondays. The kitchen serves 11:30 to 14:00 and "
    "18:00 to 21:30. Vegetarian dishes, a terrace on the Rhine, children welcome. Groups of "
    "up to 8 can book, bigger ones call us. Free cancellation until 2 hours before."
)


class _LlmEnv(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-6.1-terra"
    openai_reasoning_effort: str | None = "low"
    builder_reasoning_effort: str | None = "none"


class _Platform(NamedTuple):
    say: Callable[[str], Awaitable[list[dict[str, Any]]]]
    built: list[BuildAgentInput]
    crawled: list[str]
    published: list[str]


def _platform(engine: AsyncEngine, tmp_path: Path) -> _Platform:
    """The real assistant and builder on OpenAI and Postgres, with a fake crawl."""
    env = _LlmEnv()
    assert env.openai_api_key is not None
    openai = AsyncOpenAI(api_key=env.openai_api_key.get_secret_value(), max_retries=0)
    crawled: list[str] = []

    async def crawl(url: str, on_event: StepEmitter) -> CrawlResult:
        crawled.append(url)
        await on_event(StepEvent(step="crawl", status="done", detail="1 page read: homepage"))
        page = Page(url=url, title="Brasserie zum Rhein · Basel", text=HOMEPAGE_TEXT, json_ld=[])
        return CrawlResult(website=url, pages=[page])

    builder = Builder(
        OpenAIClient(openai, env.openai_model, reasoning_effort=env.builder_reasoning_effort),
        engine,
        model=env.openai_model,
        mcp_url="http://localhost:8001/mcp",
        cache=BuildCache("off"),
        crawl=crawl,
        generated_dir=tmp_path,
    )
    built: list[BuildAgentInput] = []
    published: list[str] = []
    # As on the platform: the build gives its owner a link, which keeps the session (#132).
    owners = Accounts(MemoryOwnerStore(), Signer(b"k" * 32))

    async def issue_link(slug: str) -> IssuedLink:
        return issued("http://test", await owners.issue_link_token(slug))

    async def publish_agent(slug: str, emit: Any) -> None:
        published.append(slug)
        await publish(slug, emit, engine=engine)

    async def build(arguments: BuildAgentInput, emit: StepEmitter) -> BuildAgentOutput:
        built.append(arguments)
        return await builder.build_agent(str(arguments.website), arguments.description, emit)

    assistant = Assistant(
        OpenAIClient(openai, env.openai_model, reasoning_effort=env.openai_reasoning_effort),
        AdminTools(None, build, TZ, today=date.today, issue_link=issue_link, publish=publish_agent),
        registry_profiles(engine),
        TZ,
        statuses=registry_statuses(engine),
    )
    app = create_app(
        Settings(_env_file=None, database_url="postgresql+psycopg://unused/db"),  # type: ignore[call-arg]
        assistant=assistant,
        accounts=owners,
    )
    transport = httpx.ASGITransport(app=app)

    async def say(message: str) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            response = await http.post(
                "/chat", json={"session_id": "s", "message": message}, timeout=120
            )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        return events

    return _Platform(say, built, crawled, published)


def _text(events: list[dict[str, Any]]) -> str:
    return "".join(e["delta"] for e in events if e["type"] == "text")


async def test_the_assistant_onboards_a_restaurant_in_three_messages(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    site = f"http://brasserie-{uuid4().hex[:8]}.example/"
    say, built, crawled, published = _platform(engine, tmp_path)
    slug: str | None = None
    try:
        # Facts, not wording: what matters is when build_agent runs.
        await say("Hello! I'd like an agent for my restaurant.")
        assert built == [], "built before getting the website and the description"

        await say(site)
        assert built == [], "built before getting the description"

        third = await say(DESCRIPTION)
        if not built:
            # The prompt allows one targeted follow-up question.
            third = await say("That's everything, please go ahead.")
        [arguments] = built
        assert str(arguments.website) == site
        assert crawled == [site]
        [card] = [e for e in third if e["type"] == "card" and e["kind"] == "profile"]
        output = BuildAgentOutput.model_validate(card["data"])
        slug = output.profile.slug
        assert output.profile.max_party_size == 8
        assert [(s.start, s.end) for s in output.profile.services] == [
            ("11:30", "14:00"),
            ("18:00", "21:30"),
        ]
        assert any(a.startswith("Closed on Monday") for a in output.assumptions)
        # The assistant reads the assumptions out (closing day, default floor plan).
        reply = _text(third).lower()
        assert any(word in reply for word in ("monday", "table", "assum", "default")), reply

        assert output.status == "building"
        assert published == [], "published before the merchant checked the card"

        # The merchant confirms the review card: the assistant publishes (#136).
        await say("Yes, that's all correct.")
        assert len(built) == 1, "built a second agent"
        if not published:
            await say("Please publish it now.")
        assert published == [slug]
    finally:
        if slug is not None:
            await deprovision(slug, engine=engine)


async def test_show_during_service_previews_that_moment(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Studio's chip goes to the model: it must ask for the moment, not only the pane (#154)."""
    host = f"brasserie-{uuid4().hex[:8]}.example"
    say, *_ = _platform(engine, tmp_path)
    slug: str | None = None
    try:
        events = await say(f"{host}. 24 seats, groups up to 4.")
        [card] = [e for e in events if e["type"] == "card" and e["kind"] == "profile"]
        slug = BuildAgentOutput.model_validate(card["data"]).profile.slug

        shown = await say("Show during service")
        [view] = [e for e in shown if e["type"] == "card" and e["kind"] == "view"]
        assert view["data"] == {"view": "manage", "moment": "during"}
    finally:
        if slug is not None:
            await deprovision(slug, engine=engine)


@pytest.mark.parametrize(
    "answer",
    [DESCRIPTION, "24 seats, groups up to 4.", "No description: use the defaults."],
    ids=["long", "short", "skip"],
)
async def test_studio_builds_from_the_address_and_the_answer_in_one_message(
    engine: AsyncEngine, tmp_path: Path, answer: str
) -> None:
    """Studio asks for the details itself, then sends all of it at once (#141): the model
    must build from that one message, even with the address as the merchant typed it."""
    host = f"brasserie-{uuid4().hex[:8]}.example"
    say, built, crawled, published = _platform(engine, tmp_path)
    slug: str | None = None
    try:
        events = await say(f"{host}. {answer}")
        assert built, f"no build, the model said: {_text(events)}"
        [arguments] = built
        assert host in str(arguments.website)
        assert len(crawled) == 1
        [card] = [e for e in events if e["type"] == "card" and e["kind"] == "profile"]
        output = BuildAgentOutput.model_validate(card["data"])
        slug = output.profile.slug
        assert output.status == "building"
        assert published == [], "published before the merchant checked the card"
    finally:
        if slug is not None:
            await deprovision(slug, engine=engine)
