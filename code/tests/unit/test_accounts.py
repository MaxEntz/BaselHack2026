"""Merchant accounts (#132): links, signed cookies and the invite code, without a database."""

from datetime import UTC, datetime, timedelta

import pytest

from merchant_platform.accounts import (
    Accounts,
    Owner,
    Signer,
    hash_token,
    login_url,
)
from tests.unit.account_fakes import ForgettingStore

SECRET = b"s" * 32
NOW = datetime(2026, 10, 31, 19, 40, tzinfo=UTC)


def accounts(clock: datetime = NOW, invite: str | None = None) -> tuple[Accounts, ForgettingStore]:
    store = ForgettingStore()
    return Accounts(store, Signer(SECRET, clock=lambda: clock), invite), store


async def test_a_link_opens_its_merchant_and_only_its_hash_is_stored() -> None:
    accounts_, store = accounts()

    token = await accounts_.issue_link_token("la-graine-verte", "Léa Moreau")

    stored = await store.by_slug("la-graine-verte")
    assert stored is not None
    assert stored.token_hash == hash_token(token) != token
    owner = await accounts_.owner_for_token(token)
    assert owner == Owner("la-graine-verte", "Léa Moreau", hash_token(token))
    assert await accounts_.owner_for_token(token + "x") is None


async def test_the_owner_cookie_names_its_merchant() -> None:
    accounts_, _ = accounts()
    token = await accounts_.issue_link_token("la-graine-verte")
    owner = await accounts_.owner_for_token(token)
    assert owner is not None

    cookie = accounts_.owner_cookie(owner)

    assert await accounts_.owner_from_cookie(cookie) == owner
    assert token not in cookie


async def test_a_new_link_ends_the_sessions_of_the_old_one() -> None:
    accounts_, _ = accounts()
    old = await accounts_.owner_for_token(await accounts_.issue_link_token("la-graine-verte"))
    assert old is not None
    cookie = accounts_.owner_cookie(old)

    await accounts_.issue_link_token("la-graine-verte")

    assert await accounts_.owner_from_cookie(cookie) is None


async def test_a_new_link_without_a_name_keeps_the_owners() -> None:
    accounts_, store = accounts()
    await accounts_.issue_link_token("la-graine-verte", "Léa Moreau")

    await accounts_.issue_link_token("la-graine-verte")

    stored = await store.by_slug("la-graine-verte")
    assert stored is not None
    assert stored.name == "Léa Moreau"


async def test_a_deleted_merchant_opens_for_nobody() -> None:
    accounts_, store = accounts()
    owner = await accounts_.owner_for_token(await accounts_.issue_link_token("la-graine-verte"))
    assert owner is not None
    cookie = accounts_.owner_cookie(owner)

    store.forget("la-graine-verte")

    assert await accounts_.owner_from_cookie(cookie) is None


async def test_a_forged_cookie_is_refused() -> None:
    accounts_, store = accounts()
    owner = await accounts_.owner_for_token(await accounts_.issue_link_token("la-graine-verte"))
    assert owner is not None
    cookie = accounts_.owner_cookie(owner)
    payload, _, mac = cookie.partition(".")
    other_secret = Accounts(store, Signer(b"t" * 32, clock=lambda: NOW))

    assert await accounts_.owner_from_cookie(f"{payload}x.{mac}") is None
    assert await accounts_.owner_from_cookie(f"{payload}.{mac[:-2]}") is None
    assert await accounts_.owner_from_cookie("garbage") is None
    assert await accounts_.owner_from_cookie("") is None
    assert await accounts_.owner_from_cookie(None) is None
    assert await other_secret.owner_from_cookie(cookie) is None


@pytest.mark.parametrize("days", [29, 31])
def test_signed_values_expire(days: int) -> None:
    value = Signer(SECRET, clock=lambda: NOW).sign({"slug": "x"})

    claims = Signer(SECRET, clock=lambda: NOW + timedelta(days=days)).unsign(value)

    assert claims == ({"slug": "x"} if days < 30 else None)


def test_without_an_invite_code_everyone_may_build() -> None:
    accounts_, _ = accounts(invite=None)

    assert not accounts_.needs_invite
    assert accounts_.invited(None)
    assert not accounts_.accepts_invite("anything")


def test_an_invite_cookie_holds_for_the_current_code_only() -> None:
    accounts_, _ = accounts(invite="olive-2026")
    changed, _ = accounts(invite="brick-2026")

    assert accounts_.needs_invite
    assert accounts_.accepts_invite("olive-2026")
    assert not accounts_.accepts_invite("olive-2027")
    assert not accounts_.invited(None)
    cookie = accounts_.invite_cookie()
    assert accounts_.invited(cookie)
    assert not changed.invited(cookie)
    assert not accounts_.invited(Signer(SECRET).sign({"slug": "la-graine-verte"}))


def test_the_link_goes_through_the_web_apps_api() -> None:
    assert (
        login_url("https://sesame.example/", "abc") == "https://sesame.example/api/login?token=abc"
    )
