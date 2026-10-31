"""`public.merchant_owners` on real Postgres (#132): one link per merchant, gone with it."""

import asyncio
from collections.abc import Awaitable, Callable

import owner_link
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.provisioning import deprovision
from core.config import get_settings
from core.db import merchant_owners_t
from merchant_platform.accounts import Accounts, PgOwnerStore, Signer, hash_token

pytestmark = pytest.mark.integration

MerchantFactory = Callable[..., Awaitable[str]]  # the `make_merchant` fixture


def accounts(engine: AsyncEngine) -> Accounts:
    return Accounts(PgOwnerStore(engine), Signer(b"k" * 32))


async def test_a_link_is_stored_as_its_hash_and_opens_its_merchant(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    owners = accounts(engine)

    token = await owners.issue_link_token(slug, "Léa")

    async with engine.connect() as conn:
        stored = (await conn.execute(select(merchant_owners_t))).all()
    rows = [row for row in stored if row.merchant_slug == slug]
    assert [(row.token_hash, row.owner_name) for row in rows] == [(hash_token(token), "Léa")]
    owner = await owners.owner_for_token(token)
    assert owner is not None and owner.slug == slug
    assert await owners.owner_from_cookie(owners.owner_cookie(owner)) == owner


async def test_a_new_link_replaces_the_old_one_and_keeps_the_name(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    owners = accounts(engine)
    first = await owners.issue_link_token(slug, "Léa")
    old = await owners.owner_for_token(first)
    assert old is not None

    second = await owners.issue_link_token(slug)

    assert await owners.owner_for_token(first) is None
    new = await owners.owner_for_token(second)
    assert new is not None and new.name == "Léa"
    assert await owners.owner_from_cookie(owners.owner_cookie(old)) is None


async def test_deleting_a_merchant_deletes_its_owner(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    owners = accounts(engine)
    token = await owners.issue_link_token(slug)

    await deprovision(slug, engine=engine)

    assert await owners.owner_for_token(token) is None
    async with engine.connect() as conn:
        left = await conn.scalar(
            select(func.count())
            .select_from(merchant_owners_t)
            .where(merchant_owners_t.c.merchant_slug == slug)
        )
    assert left == 0


async def test_the_owner_link_script_prints_a_link_that_opens_the_merchant(
    engine: AsyncEngine,
    make_merchant: MerchantFactory,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    slug = await make_merchant()
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("PLATFORM_WEB_URL", "https://sesame.example")
    get_settings.cache_clear()
    try:
        status = await asyncio.to_thread(owner_link.main, [slug, "--name", "Léa"])
        missing = await asyncio.to_thread(owner_link.main, ["chez-personne"])
    finally:
        get_settings.cache_clear()

    out = capsys.readouterr()
    link = out.out.strip()
    assert (status, missing) == (0, 1)
    assert link.startswith("https://sesame.example/api/login?token=")
    owner = await accounts(engine).owner_for_token(link.partition("token=")[2])
    assert owner is not None and (owner.slug, owner.name) == (slug, "Léa")
    assert "chez-personne: no such merchant" in out.err
