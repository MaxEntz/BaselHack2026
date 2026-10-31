"""The showcase site's booking form, through the platform API, on the real agent service."""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.service import PgAgentService
from core.config import Settings
from core.db import merchant_conn, reservations_t
from merchant_platform.api import create_app

pytestmark = pytest.mark.integration

MerchantFactory = Callable[..., Awaitable[str]]  # the `make_merchant` fixture
TZ = ZoneInfo("Europe/Zurich")
BOOKING = {
    "starts_at": "2026-11-02T20:00:00+01:00",
    "party_size": 4,
    "guest_name": "Sam Dupont",
    "idempotency_key": "site-1",
}


@pytest.fixture
def http(engine: AsyncEngine, database_url: str) -> httpx.AsyncClient:
    service = PgAgentService(engine, tz=TZ, clock=lambda: datetime(2026, 10, 31, 9, tzinfo=TZ))
    app = create_app(Settings(_env_file=None, database_url=database_url), agent=service)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_site_booking_is_stored_via_website(
    engine: AsyncEngine, http: httpx.AsyncClient, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()

    async with http:
        created = await http.post(f"/public/{slug}/reservations", json=BOOKING)
        replayed = await http.post(f"/public/{slug}/reservations", json=BOOKING)

    assert created.status_code == replayed.status_code == 201
    assert replayed.json() == created.json()
    assert created.json()["reservation"]["via"] == "website"
    async with merchant_conn(engine, slug) as conn:
        rows = (await conn.execute(select(reservations_t.c.id, reservations_t.c.via))).all()
    assert [(row.id, row.via) for row in rows] == [(created.json()["reservation"]["id"], "website")]


@pytest.mark.parametrize(
    ("overrides", "status", "error"),
    [
        ({"party_size": 7}, 422, "party_too_large"),
        ({"starts_at": "2026-11-02T20:15:00+01:00"}, 409, "slot_unavailable"),
    ],
)
async def test_site_booking_errors_come_from_the_service(
    http: httpx.AsyncClient,
    make_merchant: MerchantFactory,
    overrides: dict[str, object],
    status: int,
    error: str,
) -> None:
    slug = await make_merchant()

    async with http:
        response = await http.post(f"/public/{slug}/reservations", json=BOOKING | overrides)

    assert response.status_code == status
    assert response.json()["error"] == error


async def test_site_booking_at_an_unknown_merchant(http: httpx.AsyncClient) -> None:
    async with http:
        response = await http.post("/public/no-such-place/reservations", json=BOOKING)

    assert (response.status_code, response.json()["error"]) == (404, "not_found")


async def test_site_booking_at_a_merchant_still_building(
    http: httpx.AsyncClient, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(live=False)

    async with http:
        response = await http.post(f"/public/{slug}/reservations", json=BOOKING)

    assert (response.status_code, response.json()["error"]) == (404, "not_found")


ORIGIN = "http://localhost:8080"


@pytest.mark.parametrize(
    "overrides",
    [
        {"guest_name": "Sam\x00Dupont"},
        {"idempotency_key": "site\x00"},
        {"starts_at": "0001-01-01T00:00:00+01:00"},
        {"starts_at": "9999-12-31T23:30:00-05:00"},
    ],
)
async def test_site_booking_inputs_the_database_cannot_hold_are_422_with_cors(
    http: httpx.AsyncClient, make_merchant: MerchantFactory, overrides: dict[str, object]
) -> None:
    # Was a plain-text 500 without CORS header: the form could only say "unavailable" (#79).
    slug = await make_merchant()

    async with http:
        response = await http.post(
            f"/public/{slug}/reservations", json=BOOKING | overrides, headers={"Origin": ORIGIN}
        )

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_input"
    assert response.headers["access-control-allow-origin"] == ORIGIN


class _Crashing:
    async def create_reservation(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("database exploded")


async def test_a_crash_is_never_logged_as_ok(
    database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(Settings(_env_file=None, database_url=database_url), agent=_Crashing())  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    with caplog.at_level(logging.INFO, logger="merchant_platform"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            response = await http.post("/public/le-petit-zinc/reservations", json=BOOKING)

    assert response.status_code == 500
    outcomes = [
        r.__dict__["outcome"] for r in caplog.records if r.getMessage() == "public_reservation"
    ]
    assert outcomes == ["error"]
