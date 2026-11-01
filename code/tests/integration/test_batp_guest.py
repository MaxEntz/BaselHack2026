"""The guest's own AI agent (#252): its model and the merchant's, both scripted, talking over a
real BATP node. Only the person's yes at the terminal sends `confirm`."""

import json
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time
from typing import Any
from uuid import uuid4

import batp_guest
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.provisioning import set_status
from batp.client import CLOSED, BatpClient, SessionLost
from batp.messages import Intent, Request, Response
from core.db import merchant_conn, reservations_t
from core.llm import LLMUnavailable
from tests.integration.agent_fakes import (
    FALLBACK,
    TOKEN,
    TZ,
    Node,
    ScriptedLLM,
    call,
    calls,
    says,
)

pytestmark = pytest.mark.integration

MerchantFactory = Callable[..., Awaitable[str]]

DAY = date(2026, 11, 2)
GUEST = "Lena Koch"


def at(hhmm: str) -> str:
    return datetime.combine(DAY, time.fromisoformat(hhmm), tzinfo=TZ).isoformat()


class Person:
    """The terminal: what they answer to each question, and everything printed."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.asked: list[str] = []
        self.printed: list[str] = []

    async def ask(self, prompt: str) -> str:
        self.asked.append(prompt)
        return self.answers.pop(0) if self.answers else ""

    def show(self, line: str) -> None:
        self.printed.append(line)


async def booked(engine: AsyncEngine, slug: str) -> list[tuple[str, str]]:
    async with merchant_conn(engine, slug) as conn:
        rows = await conn.execute(
            select(reservations_t.c.guest_name, reservations_t.c.via).where(
                reservations_t.c.status == "confirmed"
            )
        )
    return [(row.guest_name, row.via) for row in rows]


async def a_proposal(
    engine: AsyncEngine, make_merchant: MerchantFactory, person: Person
) -> tuple[str, Person, ScriptedLLM, ScriptedLLM]:
    """The guest asks; the guest's model discovers the merchant and talks to its agent, which
    proposes a booking. What happens next is the person's answer."""
    word = f"zq{uuid4().hex[:6]}"
    slug = await make_merchant(name=f"Zinc {word}")
    request = f"Table for 2 on Monday at 20:00 for {GUEST}"
    merchant = ScriptedLLM(
        calls(call("propose_booking", starts_at=at("20:00"), party_size=2, guest_name=GUEST)),
        says("I can book 20:00 for 2 under Lena Koch. Shall I?"),
    )
    guest = ScriptedLLM(
        calls(call("discover", query=word)),
        calls(call("talk", merchant=slug, message=request)),
    )
    return slug, person, merchant, guest


class LosesTheConfirmsAnswer(BatpClient):
    """The node answers the `confirm`, but the session is lost before the answer arrives."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.confirms: list[dict[str, Any]] = []

    async def exchange(
        self,
        intent: Intent,
        merchant: str | None = None,
        params: dict[str, Any] | None = None,
        *,
        request_id: str | None = None,
    ) -> tuple[Request, Response]:
        exchanged = await super().exchange(intent, merchant, params, request_id=request_id)
        if params and "confirm" in params:
            self.confirms.append(params)
            if len(self.confirms) == 1:
                raise SessionLost(CLOSED)
        return exchanged


async def converse(
    engine: AsyncEngine,
    slug: str,
    person: Person,
    merchant: ScriptedLLM,
    guest: ScriptedLLM,
    client_type: type[BatpClient] = BatpClient,
) -> BatpClient:
    node = Node(engine, merchant)
    await node.server.start("127.0.0.1", 0)
    try:
        port = node.server.port
        async with client_type("127.0.0.1", port, batp_guest.GUEST, token=TOKEN) as client:
            agent = batp_guest.GuestAgent(
                guest, client, person.ask, person.show, TZ, token_set=True
            )
            await batp_guest.chat(agent, "A table on Monday at 8pm for 2, Lena Koch", person.ask)
    finally:
        await node.server.stop()
    return client


async def test_discover_talk_and_the_persons_yes_make_one_booking(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug, person, merchant, guest = await a_proposal(engine, make_merchant, Person("yes"))
    merchant.then(says("Booked: see you on Monday at 20:00."))
    guest.then(says("Your table is booked for Monday at 20:00."))
    await converse(engine, slug, person, merchant, guest)

    assert await booked(engine, slug) == [(GUEST, "claude")]
    assert person.asked[0].endswith("Confirm? [yes/no] ")
    assert "A table for 2 at Zinc" in person.asked[0]
    frames = "\n".join(person.printed)
    assert '"type":"hello"' in frames and '"token":"•••"' in frames and TOKEN not in frames
    assert '"intent":"DISCOVER"' in frames
    confirms = [line for line in person.printed if line.startswith("→") and '"confirm"' in line]
    assert len(confirms) == 1
    # The model is told what the person answered and what the merchant's agent did.
    told = guest.calls[2]["history"][-1]["content"]
    assert '"guest_answer": "yes"' in told and '"after_confirm"' in told
    assert person.printed[-1] == "\nagent> Your table is booked for Monday at 20:00.\n"


async def test_without_the_persons_yes_no_confirm_is_sent(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug, person, merchant, guest = await a_proposal(engine, make_merchant, Person("no"))
    guest.then(says("All right, nothing is booked."))
    await converse(engine, slug, person, merchant, guest)

    assert await booked(engine, slug) == []
    assert not [line for line in person.printed if '"confirm"' in line]
    assert "nothing was confirmed" in guest.calls[2]["history"][-1]["content"]


async def test_a_confirm_whose_answer_is_lost_is_sent_again_with_its_key(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug, person, merchant, guest = await a_proposal(engine, make_merchant, Person("yes"))
    guest.then(says("Your table is booked for Monday at 20:00."))
    client = await converse(engine, slug, person, merchant, guest, LosesTheConfirmsAnswer)

    assert isinstance(client, LosesTheConfirmsAnswer)
    first, again = client.confirms
    assert first == again
    # The node replayed its answer: one booking, and no call to the merchant's model for it.
    assert await booked(engine, slug) == [(GUEST, "claude")]
    assert len(merchant.calls) == 2
    assert f"✗ TALK: {CLOSED}" in person.printed
    assert any(line.startswith("✓ R-") and "confirmed" in line for line in person.printed)


async def test_a_paused_merchant_gives_its_own_booking_page(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    await set_status(slug, "down", engine=engine)
    guest = ScriptedLLM(
        calls(call("talk", merchant=slug, message="Table for 2 on Monday at 20:00?")),
        says("They don't take bookings through agents right now."),
    )
    person = Person()
    await converse(engine, slug, person, ScriptedLLM(), guest)

    # Printed by code, whether the model repeats it or not.
    assert f"  Book on the restaurant's own page: {FALLBACK}" in person.printed


async def test_a_model_outage_after_the_booking_still_shows_it(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug, person, merchant, guest = await a_proposal(engine, make_merchant, Person("yes"))
    merchant.then(says("Booked: see you on Monday at 20:00."))
    guest.then(LLMUnavailable("RateLimitError"))
    await converse(engine, slug, person, merchant, guest)

    assert await booked(engine, slug) == [(GUEST, "claude")]
    assert any(line.startswith("✓ R-") for line in person.printed)
    assert person.printed[-1].startswith("\nagent> (the model is unavailable: RateLimitError")


async def test_run_reads_its_token_from_batp_tokens(
    engine: AsyncEngine,
    make_merchant: MerchantFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, person, merchant, guest = await a_proposal(engine, make_merchant, Person("no"))
    guest.then(says("All right, nothing is booked."))
    node = Node(engine, merchant)
    await node.server.start("127.0.0.1", 0)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("BATP_URL", f"batp://127.0.0.1:{node.server.port}")
    monkeypatch.setenv("BATP_TOKENS", json.dumps({batp_guest.GUEST.id: TOKEN}))
    monkeypatch.setattr(batp_guest, "OpenAIClient", lambda *args, **kwargs: guest)
    monkeypatch.setattr(batp_guest, "_input", person.ask)
    try:
        assert await batp_guest.run(["A table on Monday at 8pm for 2, Lena Koch"]) == 0
    finally:
        await node.server.stop()

    printed = capsys.readouterr().out
    # The node let it in with the token from BATP_TOKENS, which is never printed.
    assert '"type":"welcome"' in printed and '"token":"•••"' in printed
    assert TOKEN not in printed
    assert person.asked[0].endswith("Confirm? [yes/no] ")
