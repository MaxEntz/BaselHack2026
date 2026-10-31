"""`make demo-booking` (#131): the demo's live booking goes through the real gateway, once."""

import asyncio
from collections.abc import Iterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import demo_booking
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from core.config import get_settings
from core.db import merchant_conn, reservations_t

pytestmark = pytest.mark.integration

TZ = ZoneInfo("Europe/Zurich")


@pytest.fixture
def script_settings(monkeypatch: pytest.MonkeyPatch, e2e_database_url: str) -> Iterator[None]:
    """The script reads its settings like every script: from the environment and `.env`."""
    monkeypatch.setenv("DATABASE_URL", e2e_database_url)
    monkeypatch.setenv("MERCHANT_TZ", "Europe/Zurich")
    monkeypatch.delenv("GATEWAY_TOKEN", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_books_kim_weber_through_the_gateway_once(
    gateway_base_url: str,
    e2e_engine: AsyncEngine,
    script_settings: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tomorrow = (datetime.now(TZ).date() + timedelta(days=1)).isoformat()
    argv = ["--url", f"{gateway_base_url}/mcp", "--date", tomorrow]

    # In a thread: the script runs its own event loop, as from `make`.
    assert await asyncio.to_thread(demo_booking.main, argv) == 0
    assert await asyncio.to_thread(demo_booking.main, argv) == 0

    first, again = capsys.readouterr().out.splitlines()
    assert first == again
    assert first.startswith(f"la-graine-verte {tomorrow} 20:00: R-")
    assert first.endswith(", 2 guests, table T1, via claude")
    async with merchant_conn(e2e_engine, "la-graine-verte") as conn:
        booked = (
            await conn.execute(
                select(reservations_t.c.party_size, reservations_t.c.via).where(
                    reservations_t.c.guest_name == "Kim Weber"
                )
            )
        ).all()
    assert booked == [(2, "claude")]


async def test_a_refused_booking_exits_with_the_error(
    gateway_base_url: str, script_settings: None, capsys: pytest.CaptureFixture[str]
) -> None:
    tomorrow = (datetime.now(TZ).date() + timedelta(days=1)).isoformat()
    argv = ["--url", f"{gateway_base_url}/mcp", "--date", tomorrow, "--party-size", "8"]

    assert await asyncio.to_thread(demo_booking.main, argv) == 1

    error = capsys.readouterr().err
    assert "party_too_large" in error
    assert "Fallback: http://localhost:8081/#reserver" in error


async def test_an_unreachable_gateway_is_reported(
    script_settings: None, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["--url", "http://127.0.0.1:9/mcp"]

    assert await asyncio.to_thread(demo_booking.main, argv) == 1

    assert "Is it up (`make gateway`)" in capsys.readouterr().err
