"""Per-address limits on the platform's public routes (#232): `/invite`, `/login`, the public
booking form. Over the limit, `429 too_many_requests` before any lookup, model or agent call."""

import logging
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from core.config import Settings
from merchant_platform import limits
from merchant_platform.accounts import Accounts, MemoryOwnerStore, Signer
from merchant_platform.api import create_app
from merchant_platform.limits import LIMITS, Limit, RateLimiter, TooManyRequests

SECRET = b"k" * 32
VISITOR = ("203.0.113.7", 50000)
NEIGHBOUR = ("198.51.100.4", 50000)
BOOKING = {
    "starts_at": "2026-11-02T20:00:00+01:00",
    "party_size": 2,
    "guest_name": "Alex Martin",
    "idempotency_key": "k-1",
}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"database_url": "postgresql+psycopg://u:p@localhost:5432/db"}
    return Settings(_env_file=None, **(values | overrides))


def app_with(limit: Limit, **collaborators: Any) -> tuple[FastAPI, Clock]:
    """The platform with `limit` on every limited route, on a clock the test moves."""
    clock = Clock()
    collaborators.setdefault("accounts", Accounts(MemoryOwnerStore(), Signer(SECRET), "olive"))
    app = create_app(settings(), **collaborators)
    app.state.rate_limits = {route: RateLimiter(limit, clock) for route in LIMITS}
    return app, clock


def client(app: FastAPI, address: tuple[str, int] = VISITOR) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=address)
    return httpx.AsyncClient(transport=transport, base_url="https://test")


# The limiter


def test_an_address_gets_its_requests_then_waits_for_the_oldest_to_expire() -> None:
    clock = Clock()
    limiter = RateLimiter(Limit(2, 60), clock)

    assert limiter.hit("a") is None
    clock.now += 10
    assert limiter.hit("a") is None
    assert limiter.hit("a") == pytest.approx(50)
    clock.now += 50
    assert limiter.hit("a") is None


def test_a_refused_request_does_not_count() -> None:
    clock = Clock()
    limiter = RateLimiter(Limit(1, 60), clock)
    limiter.hit("a")

    for _ in range(5):
        assert limiter.hit("a") is not None
    clock.now += 60

    assert limiter.hit("a") is None


def test_each_address_has_its_own_count() -> None:
    limiter = RateLimiter(Limit(1, 60), Clock())

    assert limiter.hit("a") is None
    assert limiter.hit("b") is None
    assert limiter.hit("a") is not None


def test_a_flood_of_addresses_forgets_the_oldest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "MAX_CLIENTS", 3)
    limiter = RateLimiter(Limit(1, 60), Clock())
    for address in ("a", "b", "c", "d"):
        limiter.hit(address)

    # "a" was forgotten when "d" came, so it starts again; "d" is still counted.
    assert limiter.hit("a") is None
    assert limiter.hit("d") is not None


def test_the_wait_is_said_in_whole_seconds_and_minutes() -> None:
    error = TooManyRequests(61.2)

    assert error.retry_after == 62
    assert str(error).endswith("Try again in 2 minutes.")
    assert TooManyRequests(0.1).retry_after == 1


def test_the_invitation_is_the_tightest_limit() -> None:
    assert (
        LIMITS["invite"].requests <= LIMITS["public_booking"].requests <= LIMITS["login"].requests
    )
    assert all(limit.per_seconds >= 60 for limit in LIMITS.values())


# The routes


async def test_invitation_guesses_get_429_with_the_usual_envelope() -> None:
    app, _ = app_with(Limit(2, 600))

    async with client(app) as http:
        tries = [await http.post("/invite", json={"code": f"guess-{n}"}) for n in range(3)]

    assert [response.status_code for response in tries] == [403, 403, 429]
    limited = tries[-1]
    assert limited.json() == {
        "error": "too_many_requests",
        "message": "Too many requests from this address. Try again in 10 minutes.",
    }
    assert limited.headers["retry-after"] == "600"


async def test_a_body_outside_the_schema_counts_too() -> None:
    app, _ = app_with(Limit(1, 600))

    async with client(app) as http:
        malformed = await http.post("/invite", json={"code": "x" * 500})
        right = await http.post("/invite", json={"code": "olive"})

    assert malformed.status_code == 422
    assert right.status_code == 429


async def test_another_address_is_not_held_back() -> None:
    app, _ = app_with(Limit(1, 600))

    async with client(app) as http:
        await http.post("/invite", json={"code": "guess"})
    async with client(app, NEIGHBOUR) as http:
        neighbour = await http.post("/invite", json={"code": "olive"})

    assert neighbour.status_code == 204


async def test_the_limit_lifts_once_the_window_is_over() -> None:
    app, clock = app_with(Limit(1, 600))

    async with client(app) as http:
        await http.post("/invite", json={"code": "guess"})
        held = await http.post("/invite", json={"code": "olive"})
        clock.now += 600
        lifted = await http.post("/invite", json={"code": "olive"})

    assert (held.status_code, lifted.status_code) == (429, 204)


async def test_login_tries_stop_before_the_database_lookup() -> None:
    owners = AsyncMock(spec=Accounts)
    owners.owner_for_token.return_value = None
    app, _ = app_with(Limit(2, 600), accounts=owners)

    async with client(app) as http:
        tries = [await http.get("/login", params={"token": f"t-{n}"}) for n in range(3)]

    assert [response.status_code for response in tries] == [401, 401, 429]
    assert owners.owner_for_token.await_count == 2


async def test_the_public_form_stops_before_the_agent_books() -> None:
    agent = AsyncMock()
    agent.create_reservation.side_effect = AssertionError("never reached")
    app, _ = app_with(Limit(0, 600), agent=agent)

    async with client(app) as http:
        response = await http.post("/public/le-petit-zinc/reservations", json=BOOKING)

    assert response.status_code == 429
    assert response.json()["error"] == "too_many_requests"
    agent.create_reservation.assert_not_awaited()


async def test_the_log_line_names_the_route_not_the_visitor(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app, _ = app_with(Limit(0, 600))

    with caplog.at_level(logging.WARNING, logger="merchant_platform.limits"):
        async with client(app) as http:
            await http.post("/invite", json={"code": "guess"})

    (record,) = [r for r in caplog.records if r.getMessage() == "rate_limited"]
    assert record.__dict__["route"] == "invite"
    assert VISITOR[0] not in caplog.text


async def test_each_app_counts_on_its_own() -> None:
    first, _ = app_with(Limit(1, 600))
    second = create_app(settings(), accounts=Accounts(MemoryOwnerStore(), Signer(SECRET), "olive"))

    async with client(first) as http:
        await http.post("/invite", json={"code": "olive"})
    async with client(second) as http:
        response = await http.post("/invite", json={"code": "olive"})

    assert response.status_code == 204
