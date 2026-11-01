"""A merchant's first evening on the whole stack (#153), in Chromium against the built web app.

Studio builds and publishes a merchant, its private link opens the management site in another
browser, a guest books through the gateway and the booking shows up live, a time blocked on
the Reservations page leaves the agent's answers, and a pause sends guests to the fallback.
Real Postgres, platform (`default_app`), gateway and web app; only the model and the crawl are
fakes. Needs a build of the current web sources (`make web-dist`, or `make test-stack`): CI
builds it and fails without it, a local run without it, or with an older one, is skipped.
"""

import json
import os
import re
import subprocess
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from playwright.async_api import Browser, Page, async_playwright, expect
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse
from starlette.routing import Mount, Route

from builder.crawl import CrawlResult, StepEmitter
from builder.crawl import Page as CrawledPage
from builder.extract import TOOL_NAME
from builder.llm import LLMReply, Message, OnText, ToolCall, ToolSpec
from core.config import Settings
from core.models import StepEvent
from merchant_platform.api import default_app
from tests.e2e.conftest import (
    call,
    free_port,
    offered,
    running_gateway,
    serving,
    subscribed,
    tomorrow,
)

pytestmark = [pytest.mark.integration, pytest.mark.browser]

TZ = ZoneInfo("Europe/Zurich")
WEB = Path(__file__).parents[2] / "web"
DIST = WEB / "dist"
# What `npm run build` reads: a `dist` older than one of them was built from other code.
WEB_SOURCES = ("src", "public", "index.html", "package.json", "vite.config.ts")
# Files among them that the build doesn't read: the unit tests and their setup.
WEB_TESTS = re.compile(r"\.test\.|^test-setup\.")
FIXTURE = json.loads((Path(__file__).parents[1] / "fixtures" / "le-petit-zinc.json").read_text())
WEBSITE = "http://zinc-e2e.example/"
NAME = "Zinc E2E"
# Le Petit Zinc's profile (12:00-14:00 and 19:00-22:00, groups of 6) under another name.
DRAFT = {
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
} | {"name": NAME, "fallback_url": None, "assumptions": []}
# Generous: the first build in a fresh process imports and compiles a lot.
BUILD_MS = 30_000


class FakeModel:
    """The platform's model, scripted: a merchant's first message builds the agent, other turns
    get one sentence, and the builder's extraction gets `DRAFT`."""

    async def respond(
        self,
        *,
        instructions: str,
        tools: Sequence[ToolSpec],
        history: Sequence[Message],
        on_text: OnText,
        tool_choice: str | None = None,
    ) -> LLMReply:
        if tool_choice is not None:
            return LLMReply(text="", tool_calls=(ToolCall("x", TOOL_NAME, json.dumps(DRAFT)),))
        if "Mode: onboarding" in instructions and history[-1]["role"] == "user":
            arguments = {"website": WEBSITE, "description": history[-1]["content"]}
            call = ToolCall("build", "build_agent", json.dumps(arguments))
            return LLMReply(text="", tool_calls=(call,))
        await on_text("Done.")
        return LLMReply(text="Done.")


async def fake_crawl(url: str, on_event: StepEmitter) -> CrawlResult:
    await on_event(StepEvent(step="crawl", status="running", detail="Loading homepage"))
    page = CrawledPage(url=url, title=NAME, text="Bistrot à Bâle.", json_ld=[])
    await on_event(StepEvent(step="crawl", status="done", detail="1 page read", duration_ms=5))
    return CrawlResult(website=url, pages=[page])


def web_app(platform: FastAPI) -> Starlette:
    """The built web app as Vite serves it in development: the platform under `/api`, any other
    path a file of `dist`, else the app's `index.html`."""
    root = DIST.resolve()

    async def page(request: Request) -> FileResponse:
        file = (root / request.path_params["path"]).resolve()
        if file.is_file() and file.is_relative_to(root):
            return FileResponse(file)
        return FileResponse(root / "index.html")

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        # A mounted app's lifespan doesn't run on its own: the live listener lives in it.
        async with platform.router.lifespan_context(platform):
            yield

    return Starlette(
        routes=[Mount("/api", app=platform), Route("/{path:path}", page)], lifespan=lifespan
    )


def web_source_files() -> list[Path]:
    """The files the build reads: tracked by git, so no `.DS_Store` or editor swap file, and
    without the tests. Outside a git checkout, the files there minus dotfiles and tests."""
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--", *WEB_SOURCES],
            cwd=WEB,
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        files = [WEB / name for name in listed.split("\0") if name]
    except (OSError, subprocess.CalledProcessError):
        files = [
            file
            for source in (WEB / name for name in WEB_SOURCES)
            for file in (source.rglob("*") if source.is_dir() else [source])
            if not any(part.startswith(".") for part in file.relative_to(WEB).parts)
        ]
    return [file for file in files if file.is_file() and not WEB_TESTS.search(file.name)]


def newest_web_source() -> tuple[float, Path]:
    """The most recently changed file the build reads, with its modification time."""
    return max((file.stat().st_mtime, file) for file in web_source_files())


def stale_web_dist() -> str | None:
    """Why `dist` can't be tested, if it can't: missing, or older than the sources."""
    index = DIST / "index.html"
    if not index.exists():
        return "no web/dist: run `make web-dist` first"
    changed_at, changed = newest_web_source()
    if changed_at > index.stat().st_mtime:
        return f"web/dist is older than web/{changed.relative_to(WEB)}: run `make web-dist`"
    return None


@pytest.fixture(scope="module")
def built_web() -> None:
    if message := stale_web_dist():
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)


@pytest.fixture(scope="module")
def stack(built_web: None, e2e_database_url: str) -> Iterator[tuple[str, str, FastAPI]]:
    """The web app with its platform, and the gateway, on one database: their base URLs, and
    the platform itself."""
    with running_gateway(Settings(_env_file=None, database_url=e2e_database_url)) as gateway:
        port = free_port()
        settings = Settings(
            _env_file=None,
            database_url=e2e_database_url,
            platform_web_url=f"http://127.0.0.1:{port}",
            gateway_public_url=gateway,
            builder_cache="off",
        )
        platform = default_app(settings, llm=FakeModel(), crawler=fake_crawl)
        with serving(web_app(platform), port) as web:
            yield web, gateway, platform


@pytest.fixture
async def browser() -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        yield browser
        await browser.close()


async def build_and_publish(studio: Page, web: str) -> str:
    """Studio, from the address to the published agent: the private link it showed."""
    await studio.goto(f"{web}/")
    await expect(
        studio.get_by_role("heading", name="What's your website's address?")
    ).to_be_visible()
    await studio.get_by_role("textbox", name="Message").fill(
        "zinc-e2e.example, 40 seats and groups up to 6"
    )
    await studio.keyboard.press("Enter")
    await expect(studio.get_by_text("What went into your profile")).to_be_visible(timeout=BUILD_MS)
    await expect(studio.locator(".st-status")).to_have_text("Hidden from guests")

    publish = studio.get_by_role("button", name="Publish", exact=True).first
    await expect(publish).to_be_enabled()
    await publish.click()
    await expect(studio.get_by_text("You're live. Agents get real answers now.")).to_be_visible()
    await expect(studio.locator(".st-status")).to_have_text("Live")

    link = re.search(rf"{re.escape(web)}/api/login\?token=[\w-]+", await studio.content())
    assert link, "the review shows the private link"
    return link.group(0)


async def test_a_merchants_first_evening(browser: Browser, stack: tuple[str, str, FastAPI]) -> None:
    web, gateway, platform = stack
    studio_browser = await browser.new_context()
    link = await build_and_publish(await studio_browser.new_page(), web)
    # Studio's management preview listens to the same merchant: closed, only the page below does.
    await studio_browser.close()

    # The private link opens the management site in a browser that never saw Studio.
    manage = await (await browser.new_context()).new_page()
    await manage.goto(link)
    await expect(manage).to_have_url(re.compile(rf"{re.escape(web)}/manage/[a-z0-9-]+$"))
    slug = manage.url.rsplit("/", 1)[-1]
    await expect(manage).to_have_title(re.compile(NAME))
    activity = manage.get_by_role("region", name="Recent activity")
    await expect(activity).to_be_visible()
    await expect(activity).not_to_contain_text("Lena Koch")
    await subscribed(platform.state.bus, slug)

    # A guest books through the gateway: the booking shows up live, with no reload.
    day = tomorrow()
    booked = await call(
        gateway,
        "create_reservation",
        merchant=slug,
        starts_at=datetime.combine(day, datetime.strptime("20:00", "%H:%M").time(), TZ).isoformat(),
        party_size=2,
        guest_name="Lena Koch",
        idempotency_key=str(uuid4()),
    )
    assert booked["reservation"]["status"] == "confirmed"
    # The bell counts what arrived since the page loaded (the toast gives way to the assistant
    # panel, open at this width); Home's feed reads the booking again.
    await expect(manage.get_by_role("button", name="Notifications")).to_have_text("1")
    await expect(activity).to_contain_text("Lena Koch, 2 guests")

    # A time blocked on the Reservations page leaves the agent's answers.
    assert "21:00" in await offered(gateway, slug, day, "21:00")
    await manage.goto(f"{web}/manage/{slug}/reservations?date={day.isoformat()}")
    await manage.get_by_role("button", name="Block a time").click()
    dialog = manage.get_by_role("dialog")
    await dialog.locator("label", has_text="21:00").click()
    await dialog.get_by_role("button", name="Save").click()
    await expect(manage.locator(".toast")).to_contain_text("Blocked 21:00")
    assert "21:00" not in await offered(gateway, slug, day, "21:00")

    # Paused, the agent sends guests to the merchant's own booking page.
    await manage.goto(f"{web}/manage/{slug}/agent")
    await manage.get_by_role("button", name="Pause the agent").click()
    paused = manage.get_by_text("Paused. Guests get your booking page.", exact=True)
    await expect(paused).to_be_visible()
    refused = await call(
        gateway, "get_availability", merchant=slug, date=day.isoformat(), party_size=2
    )
    assert (refused.get("error"), refused.get("fallback_url")) == ("agent_unavailable", WEBSITE)
