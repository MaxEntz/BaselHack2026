"""`TALK` on a real BATP node (#249): the merchant's AI agent behind it, with a scripted model,
a real Postgres, the request log and the live events, and the node's limits."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date, datetime, time
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.provisioning import set_status
from batp.messages import Response
from core.db import agent_requests_t, merchant_activity_t, merchant_conn, reservations_t
from core.llm import LLMReply
from merchant_node.limits import TalkLimits
from merchant_platform.events import LiveBus, LiveListener
from tests.integration.agent_fakes import (
    FALLBACK,
    TOKEN,
    Node,
    ScriptedLLM,
    call,
    calls,
    says,
)

pytestmark = pytest.mark.integration

MerchantFactory = Callable[..., Awaitable[str]]

TZ = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 10, 31, 9, tzinfo=TZ)
DAY = date(2026, 11, 2)
GUEST = "Lena Koch"
DEADLINE_S = 2  # AGENTS.md §9: a booking shows up in the merchant chat within 2 s.


def at(hhmm: str) -> str:
    return datetime.combine(DAY, time.fromisoformat(hhmm), tzinfo=TZ).isoformat()


def talk(text: str, **fields: Any) -> dict[str, Any]:
    return {"message": text, "idempotency_key": str(uuid4()), **fields}


@pytest.fixture
async def listener(database_url: str, engine: AsyncEngine) -> AsyncIterator[LiveBus]:
    bus = LiveBus()
    listener = LiveListener(database_url, engine, bus, "Europe/Zurich")
    task = asyncio.create_task(listener.run())
    await asyncio.wait_for(listener.listening.wait(), 5)
    yield bus
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def logged(engine: AsyncEngine, slug: str) -> list[Any]:
    """The request log's rows for `slug`."""
    async with engine.connect() as conn:
        result = await conn.execute(
            select(agent_requests_t)
            .where(agent_requests_t.c.merchant_slug == slug)
            .order_by(agent_requests_t.c.id)
        )
        return list(result)


async def rows_logged(engine: AsyncEngine, slug: str, count: int) -> list[Any]:
    """The rows, once the recorder, which runs after each answer, wrote `count` of them."""
    for _ in range(100):
        rows = await logged(engine, slug)
        if len(rows) >= count:
            return rows
        await asyncio.sleep(0.05)
    raise AssertionError(f"the request log has {len(rows)} rows, not {count}")


async def test_a_booking_through_talk_is_one_row_one_event_one_activity_line(
    engine: AsyncEngine, make_merchant: MerchantFactory, listener: LiveBus
) -> None:
    slug = await make_merchant()
    llm = ScriptedLLM(
        calls(call("get_availability", date=DAY.isoformat(), time="20:00", party_size=2)),
        says("Monday: 19:30, 20:00 or 20:30. Which name?"),
        calls(call("propose_booking", starts_at=at("20:00"), party_size=2, guest_name=GUEST)),
        says("A table for 2 on Monday at 20:00, under Lena Koch. Shall I book it?"),
        says("Booked: see you on Monday."),
    )
    queue = listener.subscribe(slug)
    async with Node(engine, llm) as client:
        asked = await client.request("TALK", slug, talk("Table for 2 on Monday around 8pm?"))
        assert asked["slots"] and asked["tools_called"] == ["get_availability"]
        conversation = asked["conversation"]
        proposed = await client.request(
            "TALK", slug, talk("20:00, Lena Koch", conversation=conversation)
        )
        assert proposed["pending"]["action"] == "book" and proposed["reservation"] is None
        done = await client.request(
            "TALK",
            slug,
            talk("Yes", conversation=conversation, confirm=proposed["pending"]["id"]),
        )

    assert done["reservation"]["status"] == "confirmed"
    event = await asyncio.wait_for(queue.get(), DEADLINE_S)
    assert (event.kind, event.via, event.reservation.id) == (
        "new_booking",
        "claude",
        done["reservation"]["id"],
    )
    async with merchant_conn(engine, slug) as conn:
        booked = list(await conn.execute(select(reservations_t.c.id, reservations_t.c.via)))
    assert booked == [(done["reservation"]["id"], "claude")]
    async with engine.connect() as conn:
        lines = list(
            await conn.execute(
                select(merchant_activity_t.c.kind, merchant_activity_t.c.reservation_id).where(
                    merchant_activity_t.c.merchant_slug == slug
                )
            )
        )
    assert [line.reservation_id for line in lines] == [done["reservation"]["id"]]

    rows = await rows_logged(engine, slug, 3)
    assert [(row.intent, row.status) for row in rows] == [("TALK", 200)] * 3
    for row in rows:
        assert TOKEN not in row.request and TOKEN not in row.response
    assert proposed["pending"]["id"] in rows[2].request


async def test_a_down_merchant_gets_its_fallback_url_and_no_model_call(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    await set_status(slug, "down", engine=engine)
    llm = ScriptedLLM()
    async with Node(engine, llm) as client:
        _, response = await client.exchange("TALK", slug, talk("Table for 2?"))

    assert response.status == 503 and response.error is not None
    assert (response.error.code, response.error.fallback_url) == ("agent_unavailable", FALLBACK)
    assert llm.calls == []


async def test_a_model_outage_is_503_with_the_fallback_url(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    from core.llm import LLMUnavailable

    slug = await make_merchant()
    async with Node(engine, ScriptedLLM(LLMUnavailable("InternalServerError"))) as client:
        _, response = await client.exchange("TALK", slug, talk("Table for 2?"))

    assert response.status == 503 and response.error is not None
    assert (response.error.code, response.error.fallback_url) == ("agent_unavailable", FALLBACK)


async def test_an_expired_conversation_is_404(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    llm = ScriptedLLM()
    async with Node(engine, llm) as client:
        _, response = await client.exchange("TALK", slug, talk("Hello?", conversation="c-gone"))

    assert response.status == 404 and response.error is not None
    assert response.error.code == "not_found"
    assert llm.calls == []


async def test_past_the_merchants_minute_limit_429_and_no_model_call(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    llm = ScriptedLLM(says("Yes."), says("Yes."))
    async with Node(engine, llm, TalkLimits(per_minute=2)) as client:
        for _ in range(2):
            await client.request("TALK", slug, talk("Open on Monday?"))
        _, response = await client.exchange("TALK", slug, talk("Open on Monday?"))

    assert response.status == 429 and response.error is not None
    assert response.error.code == "too_many_requests"
    assert len(llm.calls) == 2


async def test_past_the_turns_at_once_429_and_no_model_call(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    release, started = asyncio.Event(), asyncio.Event()

    class HeldLLM(ScriptedLLM):
        async def respond(self, **kwargs: Any) -> LLMReply:
            self.calls.append(kwargs)
            started.set()
            await release.wait()
            return says("Yes.")

    llm = HeldLLM()
    async with Node(engine, llm, TalkLimits(at_once=1)) as client:
        first = asyncio.create_task(client.exchange("TALK", slug, talk("Open on Monday?")))
        await asyncio.wait_for(started.wait(), 5)
        _, refused = await client.exchange("TALK", slug, talk("And Tuesday?"))
        release.set()
        _, answered = await first

    assert refused.status == 429 and refused.error is not None
    assert refused.error.code == "too_many_requests"
    assert answered.status == 200
    assert len(llm.calls) == 1


async def test_without_a_key_talk_is_503_and_the_other_intents_answer(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    async with Node(engine, None) as client:
        _, response = await client.exchange("TALK", slug, talk("Table for 2?"))
        slots = await client.request(
            "AVAILABILITY", slug, {"date": DAY.isoformat(), "party_size": 2}
        )

    assert isinstance(response, Response)
    assert response.status == 503 and response.error is not None
    assert (response.error.code, response.error.fallback_url) == ("agent_unavailable", FALLBACK)
    assert slots["slots"]
