"""`make demo-booking` (#131): the demo's live booking goes through the merchant's agent, once.

The gateway is bridged to a node whose agent runs on a scripted model (#254): the script must
confirm only the booking it asked for, and only once.
"""

import asyncio
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import demo_booking
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.service import PgAgentService
from core.db import merchant_conn, reservations_t
from tests.integration.agent_fakes import ScriptedLLM, calls, says
from tests.integration.agent_fakes import call as tool_call

pytestmark = pytest.mark.integration

TZ = ZoneInfo("Europe/Zurich")
GRAINE = "la-graine-verte"


@pytest.fixture
def script_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The script talks to the gateway only: it needs no `DATABASE_URL`."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("MERCHANT_TZ", "Europe/Zurich")
    monkeypatch.delenv("GATEWAY_TOKEN", raising=False)


def tomorrow() -> str:
    """The service refuses past times, and the clock is real."""
    return (datetime.now(TZ).date() + timedelta(days=1)).isoformat()


def at(hhmm: str) -> str:
    day = datetime.fromisoformat(tomorrow()).date()
    return datetime.combine(day, datetime.strptime(hhmm, "%H:%M").time(), TZ).isoformat()


def proposes(llm: ScriptedLLM, hhmm: str, guest: str, party_size: int = 2) -> None:
    """The agent's model proposes this booking; code writes the summary the script checks."""
    llm.then(
        calls(
            tool_call(
                "propose_booking", starts_at=at(hhmm), party_size=party_size, guest_name=guest
            )
        ),
        says("Shall I book it?"),
    )


async def run(argv: list[str]) -> int:
    """In a thread: the script runs its own event loop, as from `make`."""
    return await asyncio.to_thread(demo_booking.main, argv)


async def guests_rows(engine: AsyncEngine, guest: str) -> list[tuple[str, int, str]]:
    async with merchant_conn(engine, GRAINE) as conn:
        result = await conn.execute(
            select(
                reservations_t.c.status, reservations_t.c.party_size, reservations_t.c.via
            ).where(reservations_t.c.guest_name == guest)
        )
        return [tuple(row) for row in result]


async def test_books_kim_weber_through_the_agent_once(
    talking_gateway: str,
    agent_llm: ScriptedLLM,
    e2e_engine: AsyncEngine,
    script_settings: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = ["--url", f"{talking_gateway}/mcp", "--date", tomorrow()]
    proposes(agent_llm, "20:00", "Kim Weber")
    agent_llm.then(says("Booked: see you tomorrow."))

    assert await run(argv) == 0
    assert await run(argv) == 0  # listed: no model call, no booking

    first, again = capsys.readouterr().out.splitlines()
    assert first == again
    assert first.startswith(f"la-graine-verte {tomorrow()} 20:00: R-")
    assert first.endswith(", 2 guests, table T1, via claude, confirmed")
    assert await guests_rows(e2e_engine, "Kim Weber") == [("confirmed", 2, "claude")]
    asked = agent_llm.calls[0]["history"][0]["content"]
    assert "a table for 2" in asked and "20:00" in asked and "Kim Weber" in asked


async def test_a_cancelled_booking_is_booked_again(
    talking_gateway: str,
    agent_llm: ScriptedLLM,
    e2e_engine: AsyncEngine,
    script_settings: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A rehearsal cancels the live booking: the next run books a new one."""
    guest = f"Kim {uuid4().hex[:8]}"
    argv = ["--url", f"{talking_gateway}/mcp", "--date", tomorrow(), "--time", "19:00"]
    argv += ["--guest", guest]
    for _ in range(2):
        proposes(agent_llm, "19:00", guest)
        agent_llm.then(says("Booked."))
    assert await run(argv) == 0
    booked = capsys.readouterr().out.split(": ")[1].split(",")[0]
    await PgAgentService(e2e_engine, tz=TZ).cancel_reservation(GRAINE, booked, by="merchant")

    assert await run(argv) == 0

    fresh = capsys.readouterr().out
    assert fresh.endswith(", confirmed\n")
    assert f": {booked}," not in fresh
    assert sorted(await guests_rows(e2e_engine, guest)) == [
        ("cancelled", 2, "claude"),
        ("confirmed", 2, "claude"),
    ]


async def test_another_proposal_is_never_confirmed(
    talking_gateway: str,
    agent_llm: ScriptedLLM,
    e2e_engine: AsyncEngine,
    script_settings: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    guest = f"Kim {uuid4().hex[:8]}"
    proposes(agent_llm, "19:30", guest)  # not the 20:00 asked for

    argv = ["--url", f"{talking_gateway}/mcp", "--date", tomorrow(), "--guest", guest]
    assert await run(argv) == 1

    error = capsys.readouterr().err
    assert "the agent proposed no such booking. It said:\nShall I book it?" in error
    assert await guests_rows(e2e_engine, guest) == []


async def test_a_question_instead_of_a_proposal_exits_with_the_reply(
    talking_gateway: str,
    agent_llm: ScriptedLLM,
    script_settings: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    agent_llm.then(says("For how many guests, and which day?"))

    argv = ["--url", f"{talking_gateway}/mcp", "--date", tomorrow(), "--guest", "Kim Lee"]
    assert await run(argv) == 1

    assert "For how many guests, and which day?" in capsys.readouterr().err


async def test_a_refused_booking_exits_with_the_fallback_url(
    talking_gateway: str,
    agent_llm: ScriptedLLM,
    script_settings: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    proposes(agent_llm, "20:00", "Kim Weber", party_size=8)  # above La Graine Verte's 4

    argv = ["--url", f"{talking_gateway}/mcp", "--date", tomorrow(), "--party-size", "8"]
    assert await run(argv) == 1

    error = capsys.readouterr().err
    assert "the agent proposed no such booking" in error
    assert "Fallback: http://localhost:8081/#reserver" in error


async def test_without_the_agent_s_model_it_exits_with_the_fallback_url(
    gateway_base_url: str, script_settings: None, capsys: pytest.CaptureFixture[str]
) -> None:
    # Neither of these gateways reaches a model: the gateway's own refusal is printed.
    argv = ["--url", f"{gateway_base_url}/mcp", "--date", tomorrow(), "--guest", "Kim Down"]

    assert await run(argv) == 1

    error = capsys.readouterr().err
    assert "agent_unavailable: " in error
    assert "Fallback: http://localhost:8081/#reserver" in error


async def test_an_unreachable_gateway_is_reported(
    script_settings: None, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["--url", "http://127.0.0.1:9/mcp"]

    assert await run(argv) == 1

    assert "Is it up (`make gateway`)" in capsys.readouterr().err
