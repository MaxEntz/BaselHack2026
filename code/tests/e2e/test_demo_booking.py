"""`make demo-booking` (#131): the demo's live booking goes through the real gateway, once."""

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

pytestmark = pytest.mark.integration

TZ = ZoneInfo("Europe/Zurich")


@pytest.fixture
def script_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The script talks to the gateway only: it needs no `DATABASE_URL`."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("MERCHANT_TZ", "Europe/Zurich")
    monkeypatch.delenv("GATEWAY_TOKEN", raising=False)


def tomorrow() -> str:
    """The service refuses past times, and the clock is real."""
    return (datetime.now(TZ).date() + timedelta(days=1)).isoformat()


async def run(argv: list[str]) -> int:
    """In a thread: the script runs its own event loop, as from `make`."""
    return await asyncio.to_thread(demo_booking.main, argv)


async def test_books_kim_weber_through_the_gateway_once(
    gateway_base_url: str,
    e2e_engine: AsyncEngine,
    script_settings: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = ["--url", f"{gateway_base_url}/mcp", "--date", tomorrow()]

    assert await run(argv) == 0
    assert await run(argv) == 0

    first, again = capsys.readouterr().out.splitlines()
    assert first == again
    assert first.startswith(f"la-graine-verte {tomorrow()} 20:00: R-")
    assert first.endswith(", 2 guests, table T1, via claude, confirmed")
    async with merchant_conn(e2e_engine, "la-graine-verte") as conn:
        booked = (
            await conn.execute(
                select(reservations_t.c.party_size, reservations_t.c.via).where(
                    reservations_t.c.guest_name == "Kim Weber"
                )
            )
        ).all()
    assert booked == [(2, "claude")]


async def test_a_cancelled_booking_is_not_taken_for_a_new_one(
    gateway_base_url: str,
    e2e_engine: AsyncEngine,
    script_settings: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A rehearsal cancels the live booking; the next run must not pass a replay off as new."""
    guest = f"Kim {uuid4().hex[:8]}"  # each gateway mode books its own
    argv = ["--url", f"{gateway_base_url}/mcp", "--date", tomorrow(), "--time", "19:00"]
    argv += ["--guest", guest]
    assert await run(argv) == 0
    booked = capsys.readouterr().out.split(": ")[1].split(",")[0]
    await PgAgentService(e2e_engine, tz=TZ).cancel_reservation(
        "la-graine-verte", booked, by="merchant"
    )

    assert await run(argv) == 1
    error = capsys.readouterr().err
    assert f": {booked}, 2 guests" in error
    assert ", cancelled\n" in error
    assert 'BOOKING="--key 2"' in error

    assert await run([*argv, "--key", "2"]) == 0
    fresh = capsys.readouterr().out
    assert fresh.endswith(", confirmed\n")
    assert f": {booked}," not in fresh


async def test_a_refused_booking_exits_with_the_error(
    gateway_base_url: str, script_settings: None, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["--url", f"{gateway_base_url}/mcp", "--date", tomorrow(), "--party-size", "8"]

    assert await run(argv) == 1

    error = capsys.readouterr().err
    assert "party_too_large" in error
    assert "Fallback: http://localhost:8081/#reserver" in error


async def test_an_unreachable_gateway_is_reported(
    script_settings: None, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["--url", "http://127.0.0.1:9/mcp"]

    assert await run(argv) == 1

    assert "Is it up (`make gateway`)" in capsys.readouterr().err
