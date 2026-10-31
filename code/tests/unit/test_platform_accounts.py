"""Merchant accounts over HTTP (#132): the secret link, the owner cookie, the invite code."""

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from core.config import Settings
from core.models import MerchantProfile, TextEvent
from merchant_platform.accounts import (
    FINGERPRINT_CHARS,
    INVITE_COOKIE,
    OWNER_COOKIE,
    Accounts,
    MemoryOwnerStore,
    Signer,
    hash_token,
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
    assert after.status_code == 503  # past the owner check: no agent service here


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


# A session is bound to its owner's current link (#132 review)


async def signed_in(owners: Accounts, slug: str = SLUG) -> dict[str, str]:
    owner = await owners.owner_for_token(await owners.issue_link_token(slug))
    assert owner is not None
    return {OWNER_COOKIE: owners.owner_cookie(owner)}


async def chat(http: httpx.AsyncClient, session_id: str = "s1", **cookies: str) -> httpx.Response:
    http.cookies.clear()
    for name, value in cookies.items():
        http.cookies.set(name, value)
    return await http.post("/chat", json={"session_id": session_id, "message": "tonight?"})


def remembering_app(owners: Accounts) -> FastAPI:
    async def assistant(session: Session, message: str, emit: Emit) -> None:
        session.messages.append({"role": "user", "content": message})
        await emit(
            TextEvent(delta=f"merchant={session.merchant_slug} turns={len(session.messages)}")
        )

    return create_app(settings(), accounts=owners, assistant=assistant, profiles=profiles(SLUG))


def building_app(owners: Accounts) -> FastAPI:
    """An assistant that builds SLUG on the first message, as `tools._build` attaches it."""

    async def assistant(session: Session, message: str, emit: Emit) -> None:
        if session.merchant_slug is None:
            token = await owners.issue_link_token(SLUG)
            session.merchant_slug, session.link = SLUG, hash_token(token)[:FINGERPRINT_CHARS]
            session.claimable_until = time.monotonic() + 60
        session.messages.append({"role": "user", "content": message})
        await emit(TextEvent(delta=f"merchant={session.merchant_slug}"))

    return create_app(settings(), accounts=owners, assistant=assistant, profiles=profiles(SLUG))


async def test_a_cookie_bound_session_needs_its_cookie() -> None:
    owners = accounts()
    app = remembering_app(owners)
    cookie = await signed_in(owners)

    async with client(app) as http:
        first = await chat(http, **cookie)
        without = await chat(http)
        again = await chat(http, **cookie)
        await owners.issue_link_token(SLUG)  # the owner asked for a new link
        stale = await chat(http, **cookie)

    assert "turns=1" in first.text
    assert (without.status_code, without.json()["error"]) == (401, "not_signed_in")
    assert "turns=2" in again.text
    assert stale.status_code == 401


async def test_a_new_link_ends_the_session_that_built_the_merchant() -> None:
    owners = accounts()
    app = building_app(owners)

    async with client(app) as http:
        built = await chat(http, session_id="builder")
        before = await chat(http, session_id="builder")  # before its cookie is claimed
        await owners.issue_link_token(SLUG)
        revoked = await chat(http, session_id="builder")

    assert f"merchant={SLUG}" in built.text and before.status_code == 200
    assert revoked.status_code == 401
    assert app.state.sessions.find("builder") is None  # forgotten with its history


async def test_a_deleted_merchant_ends_its_chat_sessions() -> None:
    store = MemoryOwnerStore()
    owners = Accounts(store, Signer(SECRET))
    app = remembering_app(owners)
    cookie = await signed_in(owners)

    async with client(app) as http:
        await chat(http, **cookie)
        store.forget(SLUG)
        after = await chat(http)

    assert after.status_code == 401


async def test_a_session_refuses_another_merchants_cookie() -> None:
    owners = accounts()
    app = remembering_app(owners)
    mine = await signed_in(owners)
    theirs = await signed_in(owners, "maison-albane")

    async with client(app) as http:
        await chat(http, **mine)
        other = await chat(http, **theirs)

    assert (other.status_code, other.json()["error"]) == (403, "other_merchant")


async def test_there_is_no_shared_default_session_outside_development() -> None:
    async with client(create_app(settings(), accounts=accounts())) as http:
        response = await http.post("/chat", json={"message": "hi"})

    assert (response.status_code, response.json()["error"]) == (422, "invalid_input")


async def test_an_uninvited_caller_leaves_no_session_behind() -> None:
    app = create_app(settings(), accounts=accounts(invite=INVITE), profiles=profiles())

    async with client(app) as http:
        refused = await chat(http, session_id="probe-1")

    assert refused.status_code == 403
    assert app.state.sessions.find("probe-1") is None


async def test_session_reports_nothing_for_a_revoked_session() -> None:
    owners = accounts()
    app = remembering_app(owners)
    cookie = await signed_in(owners)

    async with client(app) as http:
        await chat(http, **cookie)
        http.cookies.clear()
        before = await http.get("/session", params={"session_id": "s1"})
        await owners.issue_link_token(SLUG)
        after = await http.get("/session", params={"session_id": "s1"})
        stranger = await http.get("/session", params={"session_id": "someone-else"})

    assert before.json()["merchant"]["slug"] == SLUG
    assert after.json() == stranger.json() == {"merchant": None}


# Forged and expired cookies over HTTP


async def test_a_forged_cookie_with_a_non_ascii_mac_is_refused_not_a_crash() -> None:
    app = create_app(settings(), accounts=accounts(invite=INVITE), profiles=profiles())
    transport = httpx.ASGITransport(app=app)
    # Raw bytes: a browser or a proxy can send any; Starlette reads them as latin-1.
    cookie = f"{OWNER_COOKIE}=a.\u00e9; {INVITE_COOKIE}=a.\u00e9".encode()
    headers = [(b"cookie", cookie)]

    async with httpx.AsyncClient(transport=transport, base_url="https://t") as http:
        page = await http.get(f"/merchants/{SLUG}/hours", headers=headers)
        talk = await http.post("/chat", json={"session_id": "s", "message": "hi"}, headers=headers)

    assert page.status_code == 401
    assert talk.status_code == 403


async def test_an_expired_cookie_is_refused_over_http() -> None:
    store = MemoryOwnerStore()
    month_ago = datetime.now(UTC) - timedelta(days=31)
    old = Accounts(store, Signer(SECRET, clock=lambda: month_ago))
    owner = await old.owner_for_token(await old.issue_link_token(SLUG))
    assert owner is not None
    app = create_app(settings(), accounts=Accounts(store, Signer(SECRET)))

    async with client(app) as http:
        http.cookies.set(OWNER_COOKIE, old.owner_cookie(owner))
        response = await http.get(f"/merchants/{SLUG}/hours")

    assert response.status_code == 401


async def test_another_merchants_cookie_is_refused_on_every_page() -> None:
    owners = accounts()
    app = create_app(settings(), accounts=owners)

    async with client(app) as http:
        for name, value in (await signed_in(owners, "maison-albane")).items():
            http.cookies.set(name, value)
        page = await http.get(f"/merchants/{SLUG}/hours")
        events = await http.get("/events", params={"merchant": SLUG})

    assert (page.status_code, page.json()["error"]) == (403, "other_merchant")
    assert (events.status_code, events.json()["error"]) == (403, "other_merchant")


# The building browser gets its cookie


async def test_the_browser_that_built_a_merchant_claims_its_cookie_once() -> None:
    owners = accounts()
    app = building_app(owners)

    async with client(app) as http:
        await chat(http, session_id="builder")
        claimed = await http.post("/session/claim", json={"session_id": "builder"})
        page = await http.get(f"/merchants/{SLUG}/hours")
        again = await http.post("/session/claim", json={"session_id": "builder"})
        http.cookies.clear()
        bare = await chat(http, session_id="builder")
        stranger = await http.post("/session/claim", json={"session_id": "nobody"})

    assert claimed.status_code == 204
    assert OWNER_COOKIE in claimed.headers["set-cookie"]
    assert page.status_code == 503  # past the owner check (no agent here), with the cookie
    assert again.status_code == 401  # once
    assert bare.status_code == 401  # the session now needs that cookie
    assert stranger.status_code == 401


async def test_a_session_signed_in_by_cookie_claims_nothing() -> None:
    owners = accounts()
    app = remembering_app(owners)
    cookie = await signed_in(owners)

    async with client(app) as http:
        await chat(http, session_id="tab", **cookie)
        http.cookies.clear()
        claimed = await http.post("/session/claim", json={"session_id": "tab"})

    assert claimed.status_code == 401


async def test_a_revoked_build_claims_nothing() -> None:
    owners = accounts()
    app = building_app(owners)

    async with client(app) as http:
        await chat(http, session_id="builder")
        await owners.issue_link_token(SLUG)
        claimed = await http.post("/session/claim", json={"session_id": "builder"})

    assert claimed.status_code == 401


@pytest.mark.parametrize("session_id", ["", "default"])
async def test_no_client_reaches_the_shared_session(session_id: str) -> None:
    async with client(create_app(settings(), accounts=accounts())) as http:
        response = await http.post("/chat", json={"session_id": session_id, "message": "hi"})

    assert (response.status_code, response.json()["error"]) == (422, "invalid_input")


def test_the_access_log_masks_session_ids() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/session?session_id=3f2a-77&x=1", "1.1", 200),
        None,
    )

    RedactTokens().filter(record)

    assert "3f2a-77" not in record.getMessage()


async def test_a_development_session_claims_nothing() -> None:
    app = create_app(
        settings(platform_merchant=SLUG),
        accounts=accounts(),
        assistant=echo_merchant,
        profiles=profiles(SLUG),
    )
    async with client(app) as http:
        await chat(http, session_id="dev")
        claimed = await http.post("/session/claim", json={"session_id": "dev"})

    assert claimed.status_code == 401


# Settings a deployment must not combine


@pytest.mark.parametrize(
    "overrides",
    [
        {"platform_merchant": SLUG, "platform_web_url": "https://sesame.example"},
        {"platform_merchant": SLUG, "platform_cookie_secret": "s" * 40},
        {"platform_merchant": SLUG, "platform_invite_code": "olive-2026"},
        {"platform_cookie_secret": "too-short"},
        {"platform_invite_code": "short"},
    ],
)
def test_unsafe_account_settings_refuse_to_start(overrides: dict[str, Any]) -> None:
    with pytest.raises(RuntimeError):
        create_app(settings(**overrides))
