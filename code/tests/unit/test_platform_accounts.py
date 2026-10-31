"""Merchant accounts over HTTP (#132): the secret link, the owner cookie, the invite code."""

import logging
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from core.config import Settings
from core.models import MerchantProfile, TextEvent
from merchant_platform.accounts import (
    INVITE_COOKIE,
    Accounts,
    MemoryOwnerStore,
    Signer,
)
from merchant_platform.api import create_app
from merchant_platform.assistant import Emit
from merchant_platform.logs import RedactTokens
from merchant_platform.sessions import Session

SLUG = "la-graine-verte"
SECRET = b"k" * 32
INVITE = "olive-2026"


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"database_url": "postgresql+psycopg://u:p@localhost:5432/db"}
    return Settings(_env_file=None, **(values | overrides))  # type: ignore[call-arg]


def accounts(invite: str | None = None) -> Accounts:
    return Accounts(MemoryOwnerStore(), Signer(SECRET), invite)


def client(app: FastAPI, base_url: str = "https://test") -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url)


def profiles(*known: str) -> Any:
    async def lookup(slug: str) -> MerchantProfile | None:
        if slug not in known:
            return None
        return MerchantProfile.model_construct(slug=slug, name=slug.replace("-", " ").title())

    return lookup


async def echo_merchant(session: Session, message: str, emit: Emit) -> None:
    await emit(TextEvent(delta=f"merchant={session.merchant_slug}"))


async def test_the_link_opens_the_management_site_in_this_browser() -> None:
    owners = accounts()
    token = await owners.issue_link_token(SLUG)
    app = create_app(settings(platform_web_url="https://sesame.example"), accounts=owners)

    async with client(app) as http:
        before = await http.get(f"/merchants/{SLUG}/hours")
        login = await http.get("/login", params={"token": token})
        after = await http.get(f"/merchants/{SLUG}/hours")

    assert before.status_code == 401
    assert (login.status_code, login.headers["location"]) == (303, f"/manage/{SLUG}")
    cookie = login.headers["set-cookie"].lower()
    assert all(flag in cookie for flag in ("httponly", "secure", "samesite=lax", "path=/"))
    assert token not in login.headers["set-cookie"]
    assert after.status_code == 501  # past the owner check


async def test_cookies_are_not_secure_on_a_plain_http_web_app() -> None:
    owners = accounts()
    token = await owners.issue_link_token(SLUG)

    async with client(create_app(settings(), accounts=owners), "http://test") as http:
        login = await http.get("/login", params={"token": token})

    assert "secure" not in login.headers["set-cookie"].lower()


async def test_a_wrong_link_opens_nothing() -> None:
    owners = accounts()
    await owners.issue_link_token(SLUG)

    async with client(create_app(settings(), accounts=owners)) as http:
        response = await http.get("/login", params={"token": "guess"})

    assert response.status_code == 401
    assert "set-cookie" not in response.headers


async def test_logging_out_forgets_the_merchant() -> None:
    owners = accounts()
    token = await owners.issue_link_token(SLUG)

    async with client(create_app(settings(), accounts=owners)) as http:
        await http.get("/login", params={"token": token})
        logout = await http.post("/logout")
        after = await http.get(f"/merchants/{SLUG}/hours")

    assert logout.status_code == 204
    assert after.status_code == 401


async def test_the_token_never_reaches_a_log_line(caplog: pytest.LogCaptureFixture) -> None:
    owners = accounts()
    token = await owners.issue_link_token(SLUG)
    caplog.set_level(logging.DEBUG)

    async with client(create_app(settings(), accounts=owners)) as http:
        await http.get("/login", params={"token": token})
        await http.get("/login", params={"token": token + "x"})

    # The platform's own lines; httpx's are the test client's, which sent the token.
    platform = [record for record in caplog.records if not record.name.startswith("httpx")]
    assert platform
    assert all(token not in record.getMessage() for record in platform)
    assert all(token not in str(record.__dict__) for record in platform)


def test_the_access_log_masks_the_token() -> None:
    # Uvicorn's access line: client, method, path with its query, HTTP version, status.
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/login?token=s3cr3t&x=1", "1.1", 303),
        None,
    )

    RedactTokens().filter(record)

    assert record.getMessage() == '127.0.0.1:5000 - "GET /login?token=***&x=1 HTTP/1.1" 303'


async def test_a_session_comes_back_to_its_merchant_with_the_cookie() -> None:
    owners = accounts(invite=INVITE)
    token = await owners.issue_link_token(SLUG)
    # A new process: no session in memory, the cookie alone brings the merchant back.
    app = create_app(settings(), accounts=owners, assistant=echo_merchant, profiles=profiles(SLUG))

    async with client(app) as http:
        await http.get("/login", params={"token": token})
        session = await http.get("/session", params={"session_id": "new-tab"})
        chat = await http.post("/chat", json={"session_id": "new-tab", "message": "hi"})

    assert session.json() == {"merchant": {"slug": SLUG, "name": "La Graine Verte"}}
    assert chat.status_code == 200  # an owner needs no invitation
    assert f"merchant={SLUG}" in chat.text


async def test_without_an_invite_code_anyone_can_onboard() -> None:
    app = create_app(settings(), accounts=accounts(), assistant=echo_merchant)

    async with client(app) as http:
        response = await http.post("/chat", json={"session_id": "s", "message": "hi"})

    assert response.status_code == 200
    assert "merchant=None" in response.text


async def test_onboarding_needs_the_invite_code_before_any_model_call() -> None:
    called: list[str] = []

    async def assistant(session: Session, message: str, emit: Emit) -> None:
        called.append(message)

    app = create_app(
        settings(), accounts=accounts(invite=INVITE), assistant=assistant, profiles=profiles()
    )
    async with client(app) as http:
        refused = await http.post("/chat", json={"session_id": "s", "message": "hi"})
        wrong = await http.post("/invite", json={"code": "brick-2026"})
        right = await http.post("/invite", json={"code": INVITE})
        allowed = await http.post("/chat", json={"session_id": "s", "message": "hello"})

    assert (refused.status_code, refused.json()["error"]) == (403, "needs_invite")
    assert "invitation" in refused.json()["message"]
    assert (wrong.status_code, wrong.json()["error"]) == (403, "needs_invite")
    assert right.status_code == 204
    assert INVITE_COOKIE in right.headers["set-cookie"]
    assert allowed.status_code == 200
    assert called == ["hello"]


async def test_the_development_merchant_needs_no_invitation() -> None:
    app = create_app(
        settings(platform_merchant=SLUG),
        accounts=accounts(invite=INVITE),
        assistant=echo_merchant,
        profiles=profiles(SLUG),
    )

    async with client(app) as http:
        response = await http.post("/chat", json={"session_id": "s", "message": "hi"})

    assert response.status_code == 200


async def test_an_unknown_invite_without_a_code_configured_is_harmless() -> None:
    async with client(create_app(settings(), accounts=accounts())) as http:
        response = await http.post("/invite", json={"code": "anything"})

    assert response.status_code == 204
    assert "set-cookie" not in response.headers


async def test_cross_origin_calls_never_carry_the_cookie() -> None:
    app = create_app(settings(), accounts=accounts())

    async with client(app) as http:
        preflight = await http.options(
            f"/merchants/{SLUG}/hours",
            headers={
                "origin": "http://localhost:8080",
                "access-control-request-method": "GET",
            },
        )

    assert "access-control-allow-credentials" not in preflight.headers
