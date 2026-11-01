"""`SessionGuard` (#132): who a chat session acts for, without the HTTP layer."""

import asyncio
import time

import pytest

from core.errors import InvalidInput
from core.models import MerchantProfile
from merchant_platform.accounts import Accounts, NeedsInvite, NotSignedIn, OtherMerchant, Signer
from merchant_platform.registry import ProfileLookup
from merchant_platform.sessions import Session, SessionGuard, SessionStore
from tests.unit.test_accounts import ForgettingStore

SECRET = b"g" * 32
SLUG = "la-graine-verte"
OTHER = "maison-albane"
INVITE = "olive-2026"


def profiles(*known: str) -> ProfileLookup:
    async def lookup(slug: str) -> MerchantProfile | None:
        return MerchantProfile.model_construct(slug=slug, name=slug) if slug in known else None

    return lookup


class World:
    def __init__(
        self,
        *,
        invite: str | None = None,
        development: str | None = None,
        known: tuple[str, ...] = (SLUG, OTHER),
    ) -> None:
        self.store = ForgettingStore()
        self.accounts = Accounts(self.store, Signer(SECRET), invite)
        self.sessions = SessionStore()
        self.guard = SessionGuard(self.sessions, self.accounts, profiles(*known), development)

    async def cookie(self, slug: str = SLUG) -> str:
        """The owner cookie of `slug`'s current link."""
        owner = await self.accounts.owner_for_token(await self.accounts.issue_link_token(slug))
        assert owner is not None
        return self.accounts.owner_cookie(owner)

    async def open(
        self, session_id: str | None = "s", owner: str | None = None, invite: str | None = None
    ) -> Session:
        return await self.guard.open(session_id, owner_cookie=owner, invite_cookie=invite)


async def test_the_default_session_is_the_development_merchants_only() -> None:
    with pytest.raises(InvalidInput):
        await World().open(None)
    with pytest.raises(InvalidInput):
        await World().open("default")

    session = await World(development=SLUG).open(None)

    assert session.id == "default"


async def test_an_anonymous_onboarding_needs_the_invitation_and_keeps_no_session() -> None:
    world = World(invite=INVITE)

    with pytest.raises(NeedsInvite):
        await world.open()

    assert world.sessions.find("s") is None
    invited = await world.open(invite=world.accounts.invite_cookie())
    assert invited.merchant_slug is None


async def test_without_an_invite_code_anyone_may_onboard() -> None:
    session = await World().open()

    assert session.merchant_slug is None
    assert not session.needs_cookie


async def test_a_cookie_binds_the_session_which_then_needs_it() -> None:
    world = World(invite=INVITE)
    cookie = await world.cookie()

    session = await world.open(owner=cookie)

    assert session.merchant_slug == SLUG
    assert session.needs_cookie
    with pytest.raises(NotSignedIn):
        await world.open()
    assert await world.open(owner=cookie) is session


async def test_another_merchants_cookie_is_refused() -> None:
    world = World()
    await world.open(owner=await world.cookie(SLUG))

    with pytest.raises(OtherMerchant):
        await world.open(owner=await world.cookie(OTHER))


async def test_a_replaced_link_forgets_the_session_and_stops_its_turn() -> None:
    world = World()
    session = await world.open(owner=await world.cookie())
    session.needs_cookie = False  # a build attached it: its link alone acts for it
    session.turn = asyncio.create_task(asyncio.sleep(60))

    await world.cookie()  # a new link: the session's is no longer current
    with pytest.raises(NotSignedIn):
        await world.open()
    await asyncio.sleep(0)

    assert world.sessions.find("s") is None
    assert session.turn.cancelled()


async def test_a_deleted_merchant_ends_its_sessions() -> None:
    world = World()
    session = await world.open(owner=await world.cookie())

    world.store.forget(SLUG)

    assert not await world.guard.acts_for_its_merchant(session)


async def test_the_development_merchants_sessions_need_no_link() -> None:
    world = World(invite=INVITE, development=SLUG)

    session = await world.open()  # attached from its first message: no invitation needed

    assert await world.guard.attached(session)
    session.merchant_slug = SLUG
    assert await world.guard.acts_for_its_merchant(session)
    session.merchant_slug = OTHER  # a link-less session of another merchant acts for nobody
    assert not await world.guard.acts_for_its_merchant(session)


async def test_the_building_browser_claims_its_cookie_once() -> None:
    world = World()
    await world.cookie()
    owner = await world.accounts.owner(SLUG)
    assert owner is not None
    session = world.sessions.get("builder")
    session.merchant_slug, session.link = SLUG, owner.fingerprint
    session.claimable_until = time.monotonic() + 60

    cookie = await world.guard.claim("builder")

    assert await world.accounts.owner_from_cookie(cookie) == owner
    assert session.needs_cookie
    with pytest.raises(NotSignedIn):
        await world.guard.claim("builder")


async def test_a_late_or_unknown_claim_is_refused() -> None:
    world = World()
    session = world.sessions.get("late")
    session.merchant_slug, session.claimable_until = SLUG, time.monotonic() - 1

    for session_id in ("late", "unknown"):
        with pytest.raises(NotSignedIn):
            await world.guard.claim(session_id)


async def test_the_merchant_a_session_reports() -> None:
    world = World(development=OTHER)
    cookie = await world.cookie()

    assert (await world.guard.merchant("new", owner_cookie=None)).slug == OTHER  # type: ignore[union-attr]
    assert (await world.guard.merchant("new", owner_cookie=cookie)).slug == SLUG  # type: ignore[union-attr]
    assert world.sessions.find("new") is None  # read-only

    session = await world.open(owner=cookie)
    await world.cookie()  # its link replaced: it reports what a new session would
    session.needs_cookie = False
    reported = await world.guard.merchant("s", owner_cookie=None)

    assert reported is not None
    assert reported.slug == OTHER


async def test_without_the_registry_no_session_reports_a_merchant() -> None:
    world = World(development=SLUG)
    guard = SessionGuard(world.sessions, world.accounts, None, SLUG)

    assert await guard.merchant(None, owner_cookie=None) is None
    assert not await guard.attached(None)
