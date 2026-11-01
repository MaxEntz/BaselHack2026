"""The demo, in its order (#25, documentation/runbooks/demo.md): run it before each rehearsal.

As after `make demo-reset`, La Graine Verte and Maison Albane are live and Le Petit Zinc is
built by chat from the runbook's message: the merchant adds the cancelling rule the site
doesn't give, publishes as Studio's Publish button does, and opens the private link. A guest
finds it through the gateway with their preferences and books; the owner's live events get it
within 2 s of the call. The owner blocks 21:00 through the chat and the agent stops offering
it; a party of 8 gets the booking page, and so does an outage set as `make outage` sets it; the
site's form books "via website", live too; the owner cancels the guest's booking and the
guest's next call carries the notice.

Real Postgres, platform (`default_app`, with an invite code as on the server), BATP node and
gateway as a bridge, as deployed; only the model and the crawl are scripted, so the booking
page is the scripted draft's. The management site's own buttons (Block a time, Pause the
agent) are tests/e2e/test_web_stack.py's, in a browser.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
import seed
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from agent import provisioning
from builder.crawl import CrawlResult, StepEmitter
from builder.crawl import Page as CrawledPage
from builder.extract import TOOL_NAME
from core.config import Settings
from core.db import make_engine
from core.llm import LLMReply, Message, OnText, ToolCall, ToolSpec
from core.models import StepEvent
from merchant_platform.api import default_app
from tests.e2e.conftest import (
    TZ,
    BatpNode,
    call,
    free_port,
    offered,
    running_gateway,
    serving,
    subscribed,
    tomorrow,
)

pytestmark = pytest.mark.integration

FIXTURE = json.loads((Path(__file__).parents[1] / "fixtures" / "le-petit-zinc.json").read_text())
# Le Petit Zinc's public host on the team server (ADR-0016); the crawl is scripted.
WEBSITE = "https://le-petit-zinc.191-96-88-152.sslip.io/"
BOOKING_PAGE = f"{WEBSITE}reservation.html"
SLUG = "le-petit-zinc"
# The connector runbook's Claude profile: vegetarian, at most €€, Alex Martin.
GUEST = "Alex Martin"
PREFERENCES = {"tags": ["vegetarian"], "max_price": "€€"}
INVITE = "demo-invite-code"
# documentation/runbooks/demo.md, step 2: the message, then the one fact the merchant adds.
ONBOARDING = (
    "https://le-petit-zinc.191-96-88-152.sslip.io 44 seats on 12 tables: four for 2, six for 4, "
    "two for 6. Groups up to 6. Open every day."
)
POLICY = "Free cancellation up to 2 hours before."
# Le Petit Zinc's profile, read from the message and the site, which says nothing of cancelling.
DRAFT = {
    key: FIXTURE[key]
    for key in (
        "name",
        "location",
        "seats",
        "services",
        "max_party_size",
        "tables",
        "tags",
        "price_level",
    )
} | {"cancellation_policy": None, "fallback_url": BOOKING_PAGE, "assumptions": []}
LIVE_SECONDS = 2.0  # AGENTS.md §9: "New booking via Claude" within 2 s
BUILD_SECONDS = 30.0  # the first build in a fresh process imports and compiles a lot


class ScriptedModel:
    """The platform's model: the builder's extraction gets `DRAFT`, the onboarding message
    builds, and every later message makes the calls planned for it, then gets one sentence.
    Tool errors are kept, so that a turn can't fail unnoticed."""

    def __init__(self) -> None:
        self.planned: list[tuple[str, dict[str, Any]]] = []
        self.tool_errors: list[str] = []

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
        last = history[-1]
        if last["role"] != "user":
            self.tool_errors += [
                str(message["content"])
                for message in history
                if message["role"] == "tool" and message.get("is_error")
            ]
            await on_text("Done.")
            return LLMReply(text="Done.")
        if "Mode: onboarding" in instructions:
            arguments = {"website": WEBSITE, "description": last["content"]}
            return LLMReply(
                text="", tool_calls=(ToolCall("b", "build_agent", json.dumps(arguments)),)
            )
        calls = tuple(
            ToolCall(f"c{index}", name, json.dumps(arguments))
            for index, (name, arguments) in enumerate(self.planned)
        )
        self.planned = []
        return LLMReply(text="", tool_calls=calls)


async def fake_crawl(url: str, on_event: StepEmitter) -> CrawlResult:
    """The site's homepage and booking page, as the real crawl would read them."""
    await on_event(StepEvent(step="crawl", status="running", detail="Loading homepage"))
    pages = [
        CrawledPage(url=url, title="Le Petit Zinc", text="Bistrot à Mulhouse.", json_ld=[]),
        CrawledPage(url=BOOKING_PAGE, title="Réserver", text="Réservez une table.", json_ld=[]),
    ]
    await on_event(StepEvent(step="crawl", status="done", detail="2 pages read", duration_ms=5))
    return CrawlResult(website=url, pages=pages)


@pytest.fixture(scope="module")
async def demo_engine(e2e_database_url: str) -> AsyncIterator[AsyncEngine]:
    """`make demo-reset`'s state: every demo merchant but Le Petit Zinc, live."""
    engine = make_engine(e2e_database_url)
    for slug in seed.MERCHANTS:
        if slug != SLUG:
            await seed.seed_merchant(engine, seed.load_profile(slug))
    yield engine
    await engine.dispose()


@pytest.fixture(scope="module")
def demo_stack(
    e2e_database_url: str, demo_engine: AsyncEngine, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[str, str, FastAPI, ScriptedModel]]:
    """The node, the gateway as its bridge, and the platform: their URLs, the platform, its
    model."""
    node = BatpNode(e2e_database_url, tmp_path_factory.mktemp("batp") / "node.log")
    node.start()
    try:
        gateway_settings = Settings(
            _env_file=None, database_url=e2e_database_url, batp_url=node.url
        )
        with running_gateway(gateway_settings) as gateway:
            port = free_port()
            settings = Settings(
                _env_file=None,
                database_url=e2e_database_url,
                platform_web_url=f"http://127.0.0.1:{port}",
                gateway_public_url=gateway,
                builder_cache="off",
                platform_invite_code=INVITE,
                platform_cookie_secret="s" * 32,
            )
            model = ScriptedModel()
            platform = default_app(settings, llm=model, crawler=fake_crawl)
            with serving(platform, port) as url:
                yield url, gateway, platform, model
    finally:
        node.stop()


def at(day: date, hhmm: str) -> str:
    return datetime.combine(day, datetime.strptime(hhmm, "%H:%M").time(), TZ).isoformat()


class Merchant:
    """The merchant's browser: one chat session, and its cookies."""

    def __init__(self, http: httpx.AsyncClient, model: ScriptedModel) -> None:
        self.http = http
        self.model = model
        self.session = f"demo-{uuid4().hex}"

    async def say(self, message: str, *calls: tuple[str, dict[str, Any]]) -> list[dict[str, Any]]:
        """One turn of the chat, the model making `calls`: its events, without tool errors."""
        self.model.planned = list(calls)
        response = await self.http.post(
            "/chat",
            json={"session_id": self.session, "message": message},
            timeout=BUILD_SECONDS,
        )
        assert response.status_code == 200, response.text
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert self.model.tool_errors == [], self.model.tool_errors
        assert [e for e in events if e["type"] == "error"] == []
        assert events[-1]["type"] == "done"
        return events


def card(events: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    found = [e for e in events if e["type"] == "card" and e["kind"] == kind]
    assert found, f"no {kind} card in {[e.get('kind', e['type']) for e in events]}"
    return found[-1]


async def listen(http: httpx.AsyncClient, slug: str, into: asyncio.Queue[dict[str, Any]]) -> None:
    """The management site's live events, as `EventSource` reads them."""
    async with http.stream("GET", "/events", params={"merchant": slug}, timeout=None) as stream:
        assert stream.status_code == 200
        async for line in stream.aiter_lines():
            if line.startswith("data: "):
                await into.put(json.loads(line.removeprefix("data: ")))


async def next_live(events: asyncio.Queue[dict[str, Any]], since: float) -> dict[str, Any]:
    """The next live event, within 2 s of `since` (`time.monotonic()` before the booking)."""
    event = await asyncio.wait_for(events.get(), timeout=LIVE_SECONDS + 1)
    assert time.monotonic() - since < LIVE_SECONDS
    return event


async def test_the_demo_runs_in_its_order(
    demo_stack: tuple[str, str, FastAPI, ScriptedModel], demo_engine: AsyncEngine
) -> None:
    platform_url, gateway, platform, model = demo_stack
    day = tomorrow()
    async with (
        httpx.AsyncClient(base_url=platform_url) as studio,
        httpx.AsyncClient(base_url=platform_url) as phone,
    ):
        merchant = Merchant(studio, model)

        # Studio opens with the invitation; without it, no build starts.
        refused = await studio.post("/chat", json={"session_id": "x", "message": WEBSITE})
        assert (refused.status_code, refused.json()["error"]) == (403, "needs_invite")
        assert (await studio.post("/invite", json={"code": INVITE})).status_code == 204

        # 2. Onboarding: the address and how the room works build the agent, hidden from guests.
        built = await merchant.say(ONBOARDING)
        steps = {e["step"]: e["status"] for e in built if e["type"] == "step"}
        assert steps == {
            "crawl": "done",
            "extract": "done",
            "validate": "done",
            "provision": "done",
            "deploy": "waiting",
        }
        profile = card(built, "profile")
        assert profile["data"]["profile"]["slug"] == SLUG
        assert profile["data"]["profile"]["cancellation_policy"] is None
        found = await call(gateway, "search_merchants", query="Le Petit Zinc", near="Mulhouse")
        assert SLUG not in [m["slug"] for m in found["merchants"]]

        # The merchant adds what the site doesn't say, then publishes with the top bar's button:
        # Studio claims the owner cookie after the build, then calls the route.
        added = await merchant.say(POLICY, ("update_rules", {"cancellation_policy": POLICY}))
        change = card(added, "diff")["data"]
        assert (change["label"], change["before"], change["after"]) == ("Cancelling", None, POLICY)
        claimed = await studio.post("/session/claim", json={"session_id": merchant.session})
        assert claimed.status_code == 204, claimed.text
        published = await studio.post(f"/merchants/{SLUG}/publish")
        assert (published.status_code, published.json()["status"]) == (200, "live")

        # The private link opens the management site on another device, live events included.
        token = parse_qs(urlsplit(profile["login_url"]).query)["token"][0]
        login = await phone.get("/login", params={"token": token})
        assert (login.status_code, login.headers["location"]) == (303, f"/manage/{SLUG}")
        live: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        listening = asyncio.create_task(listen(phone, SLUG, live))
        try:
            await subscribed(platform.state.bus, SLUG)

            # 3. Claude, with the guest's preferences: Maison Albane (€€€€) is filtered out.
            found = await call(
                gateway,
                "search_merchants",
                query="table for 4 tonight",
                near="Mulhouse",
                filters=PREFERENCES,
            )
            assert [m["slug"] for m in found["merchants"]] == ["la-graine-verte", SLUG]
            assert "20:00" in await offered(gateway, SLUG, day, "20:00", party_size=4)
            started = time.monotonic()
            booked = await call(
                gateway,
                "create_reservation",
                merchant=SLUG,
                starts_at=at(day, "20:00"),
                party_size=4,
                guest_name=GUEST,
                idempotency_key=str(uuid4()),
            )
            reservation = booked["reservation"]
            assert (reservation["status"], reservation["via"]) == ("confirmed", "claude")

            # 4. The merchant's side gets it within 2 s.
            event = await next_live(live, started)
            assert (event["kind"], event["via"], event["date"]) == (
                "new_booking",
                "claude",
                day.isoformat(),
            )
            assert event["reservation"]["guest_name"] == GUEST

            listed = await merchant.say(
                "What bookings do I have tomorrow?",
                ("list_reservations", {"date": day.isoformat()}),
            )
            rows = card(listed, "reservations")["data"]["reservations"]
            assert [(r["id"], r["time"], r["party_size"]) for r in rows] == [
                (reservation["id"], "20:00", 4)
            ]
            stats = await merchant.say(
                "How busy are we tomorrow?", ("get_stats", {"date": day.isoformat()})
            )
            assert card(stats, "stats")["data"]["guests"] == 4

            # 5. "Block 21:00", through the chat: Claude users no longer get 21:00.
            assert "21:00" in await offered(gateway, SLUG, day, "21:00")
            blocked = await merchant.say(
                "Block 21:00 tomorrow", ("block_slot", {"starts_at": at(day, "21:00")})
            )
            assert "blocked" in card(blocked, "confirmation")["data"]["text"]
            assert "21:00" not in await offered(gateway, SLUG, day, "21:00")

            # 6. A party of 8 gets the site's booking page.
            too_large = await call(
                gateway, "get_availability", merchant=SLUG, date=day.isoformat(), party_size=8
            )
            assert (too_large["error"], too_large["fallback_url"]) == (
                "party_too_large",
                BOOKING_PAGE,
            )

            # 7. Outage, as `make outage` sets it: the booking page too; back live, no restart.
            await provisioning.set_status(SLUG, "down", engine=demo_engine)
            down = await call(
                gateway, "get_availability", merchant=SLUG, date=day.isoformat(), party_size=2
            )
            assert (down["error"], down["fallback_url"]) == ("agent_unavailable", BOOKING_PAGE)
            await provisioning.set_status(SLUG, "live", engine=demo_engine)
            assert await offered(gateway, SLUG, day, "19:30")

            # 8. The site's own form books "via website", and it shows up live too.
            started = time.monotonic()
            form = await phone.post(
                f"/public/{SLUG}/reservations",
                json={
                    "starts_at": at(day, "19:30"),
                    "party_size": 2,
                    "guest_name": "Léo Petit",
                    "idempotency_key": str(uuid4()),
                },
            )
            assert form.status_code == 201, form.text
            event = await next_live(live, started)
            assert (event["kind"], event["via"]) == ("new_booking", "website")

            # §9: the owner cancels the guest's booking; the guest's next call carries the notice.
            cancelled = await merchant.say(
                f"Cancel {reservation['id']}, yes I'm sure.",
                ("cancel_reservation", {"reservation_id": reservation["id"]}),
            )
            assert reservation["id"] in card(cancelled, "confirmation")["data"]["text"]
            mine = await call(gateway, "get_my_reservations", guest_name=GUEST)
            assert mine["reservations"] == []
            assert len(mine["notices"]) == 1
            assert reservation["id"] in mine["notices"][0]
            # The merchant's own change sends nothing live.
            await asyncio.sleep(0.5)
            assert live.empty()
        finally:
            listening.cancel()
            await asyncio.gather(listening, return_exceptions=True)
