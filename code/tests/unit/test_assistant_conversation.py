"""The management site's assistant panel (#140): the owner's conversation, kept by the platform
so that a reload or another device finds it."""

from datetime import date

import httpx

from core.models import (
    AdminReservationRow,
    ConfirmationCard,
    ConfirmationCardData,
    ErrorEvent,
    ReservationsCard,
    ReservationsCardData,
    StepEvent,
    TextEvent,
    ToolStartEvent,
)
from merchant_platform.sessions import MAX_ITEMS, Emit, Session, SessionStore
from merchant_platform.tool_specs import PAGE_LINKS
from tests.unit.test_platform_accounts import SLUG, accounts, client, profiles, settings, signed_in

ROW = AdminReservationRow(
    id="R-001",
    time="19:00",
    guest_name="Alex Martin",
    party_size=2,
    table_label="T1",
    via="claude",
    status="confirmed",
)


def test_events_fold_into_items_as_the_web_shows_them() -> None:
    session = Session(id="s")
    session.said("What's on tonight?")
    for event in (
        ToolStartEvent(tool="list_reservations", label="Checking…"),
        TextEvent(delta="Two "),
        TextEvent(delta="bookings."),
        ReservationsCard(data=ReservationsCardData(date=date(2026, 10, 31), reservations=(ROW,))),
        TextEvent(delta="Anything else?"),
        StepEvent(step="crawl", status="running"),
        ErrorEvent(message="Something broke."),
    ):
        session.record(event)

    assert [item.model_dump(mode="json") for item in session.items] == [
        {"type": "user", "text": "What's on tonight?"},
        {"type": "assistant", "text": "Two bookings."},
        {
            "type": "card",
            "card": {
                "type": "card",
                "kind": "reservations",
                "data": {"date": "2026-10-31", "reservations": [ROW.model_dump(mode="json")]},
            },
        },
        {"type": "assistant", "text": "Anything else?"},
        {"type": "error", "message": "Something broke."},
    ]


def test_only_the_last_items_are_kept() -> None:
    session = Session(id="s")
    for n in range(MAX_ITEMS + 5):
        session.said(str(n))

    assert len(session.items) == MAX_ITEMS
    assert session.items[0].model_dump()["text"] == "5"


def test_one_conversation_per_owner_link() -> None:
    store = SessionStore()
    first = store.for_owner(SLUG, "link-a")

    assert store.for_owner(SLUG, "link-a") is first
    assert first.merchant_slug == SLUG and first.needs_cookie
    assert len(first.id) > 30  # the platform's, not guessable
    # A new link (the old one revoked) starts another; a forgotten session too.
    assert store.for_owner(SLUG, "link-b") is not first
    store.reset(first.id)
    assert store.for_owner(SLUG, "link-a") is not first


async def test_the_panel_finds_its_conversation_after_a_reload() -> None:
    owners = accounts()

    async def assistant(session: Session, message: str, emit: Emit) -> None:
        await emit(TextEvent(delta="Done."))
        await emit(ConfirmationCard(data=ConfirmationCardData(text="21:00 is blocked tonight.")))

    from merchant_platform.api import create_app

    app = create_app(settings(), accounts=owners, assistant=assistant, profiles=profiles(SLUG))
    cookie = await signed_in(owners)

    async with client(app) as http:
        http.cookies.update(cookie)
        opened = (await http.get(f"/merchants/{SLUG}/assistant")).json()
        session_id = opened["session_id"]
        await http.post("/chat", json={"session_id": session_id, "message": "Block 21:00"})
        reloaded = (await http.get(f"/merchants/{SLUG}/assistant")).json()

        # Another device of the same owner gets the same conversation.
        other = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")
        other.cookies.update(cookie)
        async with other:
            elsewhere = (await other.get(f"/merchants/{SLUG}/assistant")).json()
        # Without the cookie, the id alone opens nothing.
        http.cookies.clear()
        stolen = await http.post("/chat", json={"session_id": session_id, "message": "Hi"})
        anonymous = await http.get(f"/merchants/{SLUG}/assistant")

    assert opened["items"] == []
    assert reloaded["session_id"] == session_id
    assert reloaded["items"] == [
        {"type": "user", "text": "Block 21:00"},
        {"type": "assistant", "text": "Done."},
        {
            "type": "card",
            "card": {
                "type": "card",
                "kind": "confirmation",
                "data": {"text": "21:00 is blocked tonight."},
            },
        },
    ]
    assert elsewhere == reloaded
    assert stolen.status_code == 401
    assert anonymous.status_code == 401


async def test_another_merchants_cookie_gets_no_conversation() -> None:
    owners = accounts()

    async def assistant(session: Session, message: str, emit: Emit) -> None:
        await emit(TextEvent(delta="ok"))

    from merchant_platform.api import create_app

    app = create_app(settings(), accounts=owners, assistant=assistant, profiles=profiles(SLUG))
    cookie = await signed_in(owners, "le-petit-zinc")

    async with client(app) as http:
        http.cookies.update(cookie)
        answer = await http.get(f"/merchants/{SLUG}/assistant")

    assert answer.status_code == 403


def test_every_page_link_has_its_sentence() -> None:
    from typing import get_args

    from core.models import ManagePage

    assert set(PAGE_LINKS) == set(get_args(ManagePage))
