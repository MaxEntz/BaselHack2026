"""Platform API over HTTP: `/chat` and `/events` SSE streams, public booking API, CORS.

The assistant and the agent service are fakes: these tests cover the HTTP layer only.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from agent.service import AgentService
from core.config import Settings
from core.errors import NotFound, PartyTooLarge, SlotUnavailable
from core.models import (
    ChatEvent,
    DoneEvent,
    LiveEvent,
    Reservation,
    TextEvent,
    ToolStartEvent,
    Via,
)
from merchant_platform.api import create_app
from merchant_platform.assistant import Emit, RunTurn
from merchant_platform.events import LiveBus
from merchant_platform.sessions import Session

SITE = "http://localhost:8080"
NOTIFICATION = Path(__file__).parents[2] / "web" / "src" / "mocks" / "notification.json"
BOOKING = {
    "starts_at": "2026-10-31T20:00:00+01:00",
    "party_size": 4,
    "guest_name": "Sam Dupont",
    "idempotency_key": "site-1",
}


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"database_url": "postgresql+psycopg://u:p@localhost:5432/db"}
    return Settings(_env_file=None, **(values | overrides))  # type: ignore[call-arg]


def frames(body: str) -> list[dict[str, Any]]:
    """The JSON payloads of an SSE body, checking each `event:` name matches its `type`."""
    payloads = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        lines = [line for line in block.split("\n") if line and not line.startswith(":")]
        if not lines:
            continue
        fields = dict(line.split(": ", 1) for line in lines)
        payload = json.loads(fields["data"])
        assert fields["event"] == payload["type"]
        payloads.append(payload)
    return payloads


def client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def scripted(*events: ChatEvent) -> RunTurn:
    async def assistant(session: Session, message: str, emit: Emit) -> None:
        for event in events:
            await emit(event)

    return assistant


class FakeAgent:
    """Just enough AgentService for the public booking API."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create_reservation(
        self,
        slug: str,
        starts_at: datetime,
        party_size: int,
        guest_name: str,
        idempotency_key: str,
        via: Via,
    ) -> Reservation:
        self.calls.append({"slug": slug, "party_size": party_size, "via": via})
        if slug != "le-petit-zinc":
            raise NotFound(f"Unknown merchant: {slug}")
        if party_size > 6:
            raise PartyTooLarge("Groups of up to 6 only.")
        if party_size == 5:
            raise SlotUnavailable("No table left at 20:00.")
        return Reservation(
            id="R-003",
            merchant=slug,
            starts_at=starts_at,
            party_size=party_size,
            table_label="T5",
            status="confirmed",
            guest_name=guest_name,
            via=via,
        )


# /chat


async def test_chat_streams_the_assistant_events_then_done() -> None:
    assistant = scripted(
        ToolStartEvent(tool="list_reservations", label="Checking tonight's bookings…"),
        TextEvent(delta="You have 2 bookings"),
        TextEvent(delta=" tonight."),
    )
    async with client(create_app(settings(), assistant=assistant)) as http:
        response = await http.post("/chat", json={"session_id": "s1", "message": "Tonight?"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert frames(response.text) == [
        {
            "type": "tool_start",
            "tool": "list_reservations",
            "label": "Checking tonight's bookings…",
        },
        {"type": "text", "delta": "You have 2 bookings"},
        {"type": "text", "delta": " tonight."},
        {"type": "done"},
    ]


async def test_chat_sends_a_single_done_when_the_assistant_sends_one() -> None:
    assistant = scripted(TextEvent(delta="Hi"), DoneEvent())
    async with client(create_app(settings(), assistant=assistant)) as http:
        response = await http.post("/chat", json={"message": "hello"})

    assert [frame["type"] for frame in frames(response.text)] == ["text", "done"]


async def test_chat_reports_a_failing_assistant_without_details() -> None:
    async def failing(session: Session, message: str, emit: Emit) -> None:
        await emit(TextEvent(delta="Let me check…"))
        raise RuntimeError("connection to db-internal:5432 refused")

    async with client(create_app(settings(), assistant=failing)) as http:
        response = await http.post("/chat", json={"message": "Tonight?"})
        # The session is free again.
        again = await http.post("/chat", json={"message": "Tonight?"})

    events = frames(response.text)
    assert [event["type"] for event in events] == ["text", "error", "done"]
    assert "Please try again" in events[1]["message"]
    assert "db-internal" not in response.text
    assert "Traceback" not in response.text
    assert again.status_code == 200


async def test_a_second_message_during_a_turn_is_rejected() -> None:
    started, release = asyncio.Event(), asyncio.Event()

    async def slow(session: Session, message: str, emit: Emit) -> None:
        # Only the first message waits: other sessions answer right away.
        if message == "a":
            started.set()
            await release.wait()
        await emit(TextEvent(delta="Done."))

    async with client(create_app(settings(), assistant=slow)) as http:
        first = asyncio.create_task(http.post("/chat", json={"session_id": "s", "message": "a"}))
        await asyncio.wait_for(started.wait(), 2)
        second = await http.post("/chat", json={"session_id": "s", "message": "b"})
        other = await http.post("/chat", json={"session_id": "other", "message": "c"})
        release.set()
        response = await first

    assert second.status_code == 409
    assert other.status_code == 200
    assert [event["type"] for event in frames(response.text)] == ["text", "done"]


async def test_the_assistant_keeps_state_in_the_session() -> None:
    async def remember(session: Session, message: str, emit: Emit) -> None:
        session.messages.append({"role": "user", "content": message})
        await emit(TextEvent(delta=str(len(session.messages))))

    app = create_app(settings(), assistant=remember)
    async with client(app) as http:
        for _ in range(2):
            response = await http.post("/chat", json={"session_id": "s", "message": "hi"})
        await http.post("/dev/reset-session", json={"session_id": "s"})
        after_reset = await http.post("/chat", json={"session_id": "s", "message": "hi"})

    assert frames(response.text)[0]["delta"] == "2"
    assert frames(after_reset.text)[0]["delta"] == "1"


async def test_chat_rejects_long_messages_in_the_tool_error_format() -> None:
    async with client(create_app(settings())) as http:
        response = await http.post("/chat", json={"message": "x" * 4001})

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_input"
    assert "x" * 100 not in response.text


async def test_dev_routes_can_be_turned_off() -> None:
    async with client(create_app(settings(platform_dev_routes=False))) as http:
        response = await http.post("/dev/reset-session", json={})

    assert response.status_code == 404


# Public booking API (showcase site form)


async def test_public_reservation_books_via_website() -> None:
    agent = FakeAgent()
    app = create_app(settings(), agent=cast(AgentService, agent))
    async with client(app) as http:
        response = await http.post("/public/le-petit-zinc/reservations", json=BOOKING)

    assert response.status_code == 201
    reservation = response.json()["reservation"]
    assert reservation["id"] == "R-003"
    assert reservation["via"] == "website"
    assert agent.calls == [{"slug": "le-petit-zinc", "party_size": 4, "via": "website"}]


@pytest.mark.parametrize(
    ("slug", "party_size", "status", "error"),
    [
        ("chez-personne", 4, 404, "not_found"),
        ("le-petit-zinc", 5, 409, "slot_unavailable"),
        ("le-petit-zinc", 8, 422, "party_too_large"),
        ("le-petit-zinc", 0, 422, "invalid_input"),
        ("Not_A_Slug", 4, 422, "invalid_input"),
    ],
)
async def test_public_reservation_errors(
    slug: str, party_size: int, status: int, error: str
) -> None:
    app = create_app(settings(), agent=cast(AgentService, FakeAgent()))
    async with client(app) as http:
        response = await http.post(
            f"/public/{slug}/reservations", json=BOOKING | {"party_size": party_size}
        )

    assert response.status_code == status
    assert response.json()["error"] == error
    assert response.json()["message"]


async def test_public_reservation_without_agent_service_is_unavailable() -> None:
    async with client(create_app(settings())) as http:
        response = await http.post("/public/le-petit-zinc/reservations", json=BOOKING)

    assert response.status_code == 503
    assert response.json()["error"] == "agent_unavailable"


@pytest.mark.parametrize(("origin", "allowed"), [(SITE, True), ("https://evil.example", False)])
async def test_cors_allows_the_showcase_site_only(origin: str, allowed: bool) -> None:
    app = create_app(settings(), agent=cast(AgentService, FakeAgent()))
    async with client(app) as http:
        preflight = await http.options(
            "/public/le-petit-zinc/reservations",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        response = await http.post(
            "/public/le-petit-zinc/reservations", json=BOOKING, headers={"Origin": origin}
        )

    for answer in (preflight, response):
        assert (answer.headers.get("access-control-allow-origin") == origin) is allowed
        if not allowed:
            assert "access-control-allow-origin" not in answer.headers


# /events, against a real server: ASGITransport can't stream an endless response.


@pytest.fixture
async def serve() -> AsyncIterator[Callable[[FastAPI], Any]]:
    servers: list[tuple[uvicorn.Server, asyncio.Task[None]]] = []

    async def start(app: FastAPI) -> str:
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve())
        await wait_for(lambda: server.started)
        servers.append((server, task))
        port = server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    yield start
    for server, task in servers:
        server.should_exit = True
        await task


async def next_frame(lines: AsyncIterator[str]) -> dict[str, Any]:
    fields: dict[str, str] = {}
    async for line in lines:
        if not line:
            if "data" in fields:
                payload: dict[str, Any] = json.loads(fields["data"])
                assert fields["event"] == payload["type"]
                return payload
            fields = {}
        elif not line.startswith(":"):
            name, _, value = line.partition(": ")
            fields[name] = value
    raise AssertionError("stream ended")


async def wait_for(condition: Callable[[], bool]) -> None:
    """Poll state owned by uvicorn or the bus, which offer no event to await."""
    async with asyncio.timeout(2):
        while not condition():  # noqa: ASYNC110
            await asyncio.sleep(0.01)


async def test_events_streams_the_merchants_notifications(serve: Any) -> None:
    bus = LiveBus()
    notification = LiveEvent.model_validate_json(NOTIFICATION.read_text())
    base_url = await serve(create_app(settings(), bus=bus))

    async with (
        httpx.AsyncClient(base_url=base_url, timeout=5) as http,
        http.stream("GET", "/events", params={"merchant": "le-petit-zinc"}) as response,
    ):
        assert response.headers["content-type"].startswith("text/event-stream")
        await wait_for(lambda: bus.subscriber_count("le-petit-zinc") == 1)
        bus.publish("chez-mimi", notification.model_copy(update={"kind": "cancelled"}))
        bus.publish("le-petit-zinc", notification)
        lines = response.aiter_lines()
        assert await next_frame(lines) == json.loads(notification.model_dump_json())

    # The stream is closed: the subscription goes away with it.
    await wait_for(lambda: bus.subscriber_count("le-petit-zinc") == 0)


async def test_events_sends_heartbeats(serve: Any) -> None:
    base_url = await serve(create_app(settings(), heartbeat_seconds=0.1))

    async with (
        httpx.AsyncClient(base_url=base_url, timeout=5) as http,
        http.stream("GET", "/events", params={"merchant": "le-petit-zinc"}) as response,
    ):
        async with asyncio.timeout(2):
            async for line in response.aiter_lines():
                if line.startswith(":"):
                    break


async def test_events_rejects_an_invalid_merchant() -> None:
    async with client(create_app(settings())) as http:
        response = await http.get("/events", params={"merchant": "../etc"})

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_input"


async def test_a_client_leaving_mid_turn_cancels_it_and_frees_the_session(serve: Any) -> None:
    cancelled = asyncio.Event()

    async def endless(session: Session, message: str, emit: Emit) -> None:
        await emit(TextEvent(delta="Reading your website…"))
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    app = create_app(settings(), assistant=endless)
    base_url = await serve(app)
    async with httpx.AsyncClient(base_url=base_url, timeout=5) as http:
        async with http.stream(
            "POST", "/chat", json={"session_id": "s", "message": "a"}
        ) as response:
            assert (await next_frame(response.aiter_lines()))["type"] == "text"
        await asyncio.wait_for(cancelled.wait(), 2)
        await wait_for(lambda: not app.state.sessions.get("s").lock.locked())
