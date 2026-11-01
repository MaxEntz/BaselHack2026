"""The guest's own AI agent (#252): its model and the merchant's, both scripted, talking over a
real BATP node. Only the person's yes at the terminal sends `confirm`."""

from collections.abc import Awaitable, Callable
from datetime import date, datetime, time
from uuid import uuid4

import batp_guest
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from core.db import merchant_conn, reservations_t
from tests.integration.agent_fakes import TOKEN, TZ, Node, ScriptedLLM, call, calls, says

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


async def converse(
    engine: AsyncEngine, slug: str, person: Person, merchant: ScriptedLLM, guest: ScriptedLLM
) -> None:
    node = Node(engine, merchant)
    await node.server.start("127.0.0.1", 0)
    try:
        async with node.client(batp_guest.GUEST) as client:
            agent = batp_guest.GuestAgent(
                guest, client, person.ask, person.show, TZ, token_set=True
            )
            await batp_guest.chat(agent, "A table on Monday at 8pm for 2, Lena Koch", person.ask)
    finally:
        await node.server.stop()


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
