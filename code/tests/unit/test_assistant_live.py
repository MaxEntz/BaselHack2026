"""The platform assistant against the real API (`llm` marker): rerun after every prompt
change. Facts, not wording: the tools called and their effect."""

import json
from datetime import datetime
from typing import Any

import pytest
from openai import AsyncOpenAI

from core.config import Settings
from core.llm import OpenAIClient
from merchant_platform.sessions import Session
from tests.unit.assistant_fakes import SLUG, TODAY, TZ, FakeAgent, Harness, onboarded


def real_llm() -> OpenAIClient:
    # `code/.env`'s model settings, as the platform reads them; these tests read no database.
    env = Settings(database_url="postgresql+psycopg://unused/db")  # type: ignore[call-arg]
    assert env.openai_api_key is not None
    client = AsyncOpenAI(api_key=env.openai_api_key.get_secret_value(), max_retries=0)
    return OpenAIClient(client, env.openai_model, reasoning_effort=env.openai_reasoning_effort)


def tools_called(session: Session, since: int = 0) -> list[tuple[str, dict[str, Any]]]:
    return [
        (call["name"], json.loads(call["arguments"]))
        for message in session.messages[since:]
        if message["role"] == "assistant"
        for call in message["tool_calls"]
    ]


async def live_turns(*messages: str, onboarded_already: bool = True) -> tuple[Harness, Session]:
    harness = Harness(real_llm(), agent=FakeAgent())
    session = onboarded() if onboarded_already else Session(id="s")
    if onboarded_already:
        harness.provisioned.add(SLUG)
    for message in messages:
        await harness.turn(session, message)
    return harness, session


@pytest.mark.llm
async def test_live_tonights_bookings() -> None:
    _, session = await live_turns("What bookings do I have tonight?")
    assert tools_called(session) == [("list_reservations", {"date": "2026-10-31"})]


@pytest.mark.llm
async def test_live_block_a_slot() -> None:
    _, session = await live_turns("Block 21:00 tonight")
    [(name, arguments)] = tools_called(session)
    assert name == "block_slot"
    starts_at = datetime.fromisoformat(arguments["starts_at"]).astimezone(TZ)
    assert (starts_at.date(), starts_at.hour, starts_at.minute) == (TODAY, 21, 0)


@pytest.mark.llm
async def test_live_max_party_size() -> None:
    _, session = await live_turns("Set max party size to 6")
    [(name, arguments)] = tools_called(session)
    assert (name, arguments["max_party_size"]) == ("update_policy", 6)


@pytest.mark.llm
async def test_live_how_busy() -> None:
    _, session = await live_turns("How busy are we tonight?")
    calls = tools_called(session)
    # Facts, not one exact path: tonight's stats, and only reads (the model may also list the
    # bookings, #109).
    assert ("get_stats", {"date": "2026-10-31"}) in calls
    assert {name for name, _ in calls} <= {"get_stats", "list_reservations"}


@pytest.mark.llm
async def test_live_cancel_asks_for_confirmation_first() -> None:
    harness, session = await live_turns("Cancel R-001")
    # No write before the yes. The model may link the page as it asks, which changes nothing.
    assert "cancel_reservation" not in [name for name, _ in tools_called(session)]
    asked = len(session.messages)
    await harness.turn(session, "Yes, cancel it")
    assert ("cancel_reservation", {"reservation_id": "R-001"}) in tools_called(session, asked)


@pytest.mark.llm
async def test_live_a_correction_goes_to_what_the_agent_knows() -> None:
    """Facts, not wording: the right tool, and the words to take out (#248)."""
    _, session = await live_turns("We no longer serve the tartare. Update what my agent knows.")
    [(name, arguments)] = tools_called(session)
    assert name == "update_knowledge"
    assert any("tartare" in word.lower() for word in arguments.get("remove") or [])


@pytest.mark.llm
async def test_live_knows_a_publish_from_the_button() -> None:
    """The history says it's hidden; the Publish button has since put it live (#198)."""
    harness = Harness(real_llm(), agent=FakeAgent())
    harness.provisioned.add(SLUG)
    session = onboarded()
    session.messages += [
        {"role": "user", "content": "Can guests book me yet?"},
        {"role": "assistant", "content": "Not yet: your agent is hidden until you publish it."},
    ]
    harness.status = "live"
    since = len(session.messages)

    await harness.turn(session, "Is my agent live now?")

    assert "publish_agent" not in [name for name, _ in tools_called(session, since)]
    reply = session.messages[-1]["content"].lower()
    assert "live" in reply
    assert not any(no in reply for no in ("not live", "isn't live", "not yet", "until you publish"))


@pytest.mark.llm
async def test_live_sends_a_paused_merchant_to_the_agent_page() -> None:
    """No tool resumes a paused agent: the Agent page's switch does (#198)."""
    harness = Harness(real_llm(), agent=FakeAgent())
    harness.provisioned.add(SLUG)
    harness.status = "down"
    session = onboarded()

    await harness.turn(session, "Resume my agent, please.")

    called = tools_called(session)
    # Markdown left out: the model may write "the **Agent** page".
    reply = session.messages[-1]["content"].lower().replace("*", "").replace("`", "")
    # The link to the page (#140), or the page named in the reply.
    assert called in ([], [("link_page", {"page": "agent"})])
    assert called or "agent page" in reply or "pause switch" in reply


@pytest.mark.llm
async def test_live_onboarding_asks_then_builds() -> None:
    harness, session = await live_turns("Hi!", onboarded_already=False)
    assert tools_called(session) == []
    await harness.turn(session, "http://localhost:8080/")
    assert tools_called(session) == []
    await harness.turn(
        session,
        "Lunch 12-14 and dinner 19-22 every day, 12 tables (four of 2, six of 4, two of 6), "
        "groups up to 6 online, vegetarian and gluten-free options, a terrace, children "
        "welcome, free cancellation until 2 hours before.",
    )
    [(name, arguments)] = tools_called(session)
    assert name == "build_agent"
    assert arguments["website"].startswith("http://localhost:8080")
    assert session.merchant_slug == SLUG


# Studio's requests with a real model (#152): facts, not wording.


@pytest.mark.llm
async def test_live_publish_publishes() -> None:
    harness, session = await live_turns("Publish my agent.")
    assert harness.published == [SLUG]
    assert [name for name, _ in tools_called(session)] == ["publish_agent"]


@pytest.mark.llm
async def test_live_change_my_website_changes_nothing() -> None:
    harness, session = await live_turns("Change the colours on my website to blue.")
    called = {name for name, _ in tools_called(session)}
    # Nothing touches the merchant's site: at most the management site's look.
    assert called <= {"update_look"}
    assert harness.published == []


@pytest.mark.llm
async def test_live_opening_hours_link_to_their_page() -> None:
    _, session = await live_turns("What are my opening hours?")
    assert ("link_page", {"page": "hours"}) in tools_called(session)
