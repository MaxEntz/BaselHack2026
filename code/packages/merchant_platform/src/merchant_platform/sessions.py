"""Merchant chat sessions, in memory (lost on restart), and who each one acts for (#132).

A session is the browser's (its id) or the owner link's (`SessionStore.for_owner`, #140).
`SessionGuard` holds the rules every chat route applies; the routes only read the cookies.
"""

import asyncio
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from core.errors import InvalidInput
from core.models import (
    AssistantItem,
    CardItem,
    ChatEvent,
    ConversationItem,
    ErrorEvent,
    ErrorItem,
    MerchantProfile,
    TextEvent,
    UserItem,
)
from merchant_platform.accounts import (
    NEEDS_INVITE,
    NOT_SIGNED_IN,
    OTHER_MERCHANT,
    Accounts,
    NeedsInvite,
    NotSignedIn,
    OtherMerchant,
    Owner,
    same,
)
from merchant_platform.registry import ProfileLookup

# Pushes one event into the merchant's `/chat` stream.
Emit = Callable[[ChatEvent], Awaitable[None]]

DEFAULT_SESSION = "default"
# What the management site's panel gets back after a reload (#140): the last items only.
MAX_ITEMS = 200
# How long after a build its browser may claim the owner cookie (`POST /session/claim`).
CLAIM_SECONDS = 300


@dataclass(eq=False)
class Session:
    id: str
    # Conversation history, in the assistant's own format (#17).
    messages: list[dict[str, Any]] = field(default_factory=list)
    merchant_slug: str | None = None
    # The owner link the session acts with (`Owner.fingerprint`, #132): set when a cookie or a
    # build attaches it, checked on each request, so a new link or a deletion ends it. None for
    # the development merchant (PLATFORM_MERCHANT).
    link: str | None = None
    # Once a cookie bound the session, or the build's browser claimed its cookie, every message
    # needs that cookie: the session id alone no longer acts for the merchant.
    needs_cookie: bool = False
    # Until when the browser that built the merchant may claim its owner cookie, once (#132).
    claimable_until: float | None = None
    # The history's length when this session built its merchant: publishing waits for a
    # merchant message after it, so the review card is always seen first (#136, #152).
    built_at: int | None = None
    # The conversation as the web shows it (#140): what the merchant said, the replies, the
    # cards and the errors, never the steps.
    items: list[ConversationItem] = field(default_factory=list)
    # Held for the whole turn: one turn at a time per session.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # The running turn, kept referenced so it isn't garbage-collected mid-flight.
    turn: asyncio.Task[None] | None = None

    def built(self, slug: str, link: str | None) -> None:
        """The session just built `slug`'s agent: it now acts with the merchant's new link
        (`Owner.fingerprint`, none for a merchant nobody owns), and its browser may claim the
        owner cookie once, within `CLAIM_SECONDS` (#132)."""
        self.merchant_slug = slug
        self.built_at = len(self.messages)
        self.link = link
        self.claimable_until = time.monotonic() + CLAIM_SECONDS if link else None

    def said(self, text: str) -> None:
        self._keep(UserItem(text=text))

    def record(self, event: ChatEvent) -> None:
        """Folds a chat event into the items, as the web's reducer does: deltas extend the
        reply they follow, a card or an error comes after it."""
        if isinstance(event, TextEvent):
            last = self.items[-1] if self.items else None
            if isinstance(last, AssistantItem):
                self.items[-1] = AssistantItem(text=last.text + event.delta)
            else:
                self._keep(AssistantItem(text=event.delta))
        elif isinstance(event, ErrorEvent):
            self._keep(ErrorItem(message=event.message))
        elif event.type == "card":
            self._keep(CardItem(card=event))

    def _keep(self, item: ConversationItem) -> None:
        self.items.append(item)
        del self.items[:-MAX_ITEMS]


# Answers one merchant message (the assistant).
RunTurn = Callable[[Session, str, Emit], Awaitable[None]]


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        # The management site's conversation of each owner link (#140): (slug, link) → id.
        self._owners: dict[tuple[str, str | None], str] = {}

    def for_owner(self, slug: str, link: str | None) -> Session:
        """The owner's own conversation, shared by every device signed in with that link. Its
        id is the platform's and unguessable; once bound, `/chat` also needs the cookie. The
        development merchant's (no link) works without one, as its sessions do."""
        known = self._sessions.get(self._owners.get((slug, link), ""))
        if known is not None and known.merchant_slug == slug and known.link == link:
            return known
        session_id = f"o-{secrets.token_urlsafe(24)}"
        session = self._sessions[session_id] = Session(id=session_id)
        session.merchant_slug, session.link, session.needs_cookie = slug, link, link is not None
        self._owners[(slug, link)] = session_id
        return session

    def get(self, session_id: str | None) -> Session:
        """The session for `session_id` (the default one when missing), created on first use."""
        key = session_id or DEFAULT_SESSION
        session = self._sessions.get(key)
        if session is None:
            session = self._sessions[key] = Session(id=key)
        return session

    def find(self, session_id: str | None) -> Session | None:
        """The session for `session_id` if it exists, without creating it."""
        return self._sessions.get(session_id or DEFAULT_SESSION)

    def all(self) -> list[Session]:
        return list(self._sessions.values())

    def reset(self, session_id: str | None) -> None:
        """Forget a session: the next message starts onboarding again."""
        self._sessions.pop(session_id or DEFAULT_SESSION, None)


async def session_merchant(
    session: Session | None, default: str | None, profiles: ProfileLookup
) -> MerchantProfile | None:
    """The merchant `session` works on, or will from its next message (#88).

    A session without a merchant takes `default` (PLATFORM_MERCHANT) once it is registered,
    so that a page reload or a seeded demo doesn't force a new onboarding (AGENTS.md §14).
    """
    slug = (session.merchant_slug if session else None) or default
    return await profiles(slug) if slug else None


class SessionGuard:
    """Who a chat session acts for, checked on every request (#132).

    A session acts with the owner link that attached it: an owner cookie, or the build that
    created its merchant. Another merchant's cookie is refused. Once a cookie bound it, every
    message needs that cookie. A session whose link was replaced, or whose merchant was deleted,
    is forgotten with its history (it holds guest names). The development merchant
    (PLATFORM_MERCHANT) needs no link. Starting a build needs the invitation when
    PLATFORM_INVITE_CODE is set.
    """

    def __init__(
        self,
        sessions: SessionStore,
        accounts: Accounts,
        profiles: ProfileLookup | None,
        development_merchant: str | None,
    ) -> None:
        """Without `profiles` (the registry), no session is attached to a merchant until a
        cookie or a build does it."""
        self._sessions = sessions
        self._accounts = accounts
        self._profiles = profiles
        self._development = development_merchant

    async def open(
        self, session_id: str | None, *, owner_cookie: str | None, invite_cookie: str | None
    ) -> Session:
        """The session a chat message goes to, bound to the cookie's owner, if it may go.

        `InvalidInput` for the shared default session without a development merchant,
        `NeedsInvite` before an onboarding without the invitation (before any model call, and
        before keeping a session for an anonymous caller), `OtherMerchant` and `NotSignedIn`
        as `bind`.
        """
        if session_id in (None, DEFAULT_SESSION) and self._development is None:
            # The shared default session is the development merchant's only (#132).
            raise InvalidInput("session_id is required")
        owner = await self._accounts.owner_from_cookie(owner_cookie)
        invited = self._accounts.invited(invite_cookie)
        known = self._sessions.find(session_id)
        if known is None and owner is None and not invited and not await self.attached(None):
            raise NeedsInvite(NEEDS_INVITE)
        session = self._sessions.get(session_id)
        await self.bind(session, owner)
        if not invited and not await self.attached(session):
            # An onboarding crawls a site and spends the OpenAI key.
            raise NeedsInvite(NEEDS_INVITE)
        return session

    async def bind(self, session: Session, owner: Owner | None) -> None:
        """A cookie attaches the session, or renews its link; another merchant's is refused. A
        session that no longer acts for its merchant is forgotten, its running turn stopped."""
        if owner is not None:
            if session.merchant_slug not in (None, owner.slug):
                raise OtherMerchant(OTHER_MERCHANT)
            session.merchant_slug, session.link = owner.slug, owner.fingerprint
            session.needs_cookie = True
            return
        if session.needs_cookie:
            raise NotSignedIn(NOT_SIGNED_IN)
        if not await self.acts_for_its_merchant(session):
            # A running turn holds the old session: stop it with the session.
            if session.turn and not session.turn.done():
                session.turn.cancel()
            self._sessions.reset(session.id)
            raise NotSignedIn(NOT_SIGNED_IN)

    async def attached(self, session: Session | None) -> bool:
        """Whether the session works on a merchant, or will from its next message."""
        if session is not None and session.merchant_slug is not None:
            return True
        if self._profiles is None:
            return False
        return await session_merchant(session, self._development, self._profiles) is not None

    async def acts_for_its_merchant(self, session: Session) -> bool:
        """The session's link is still its merchant's current one. The development merchant's
        sessions carry none."""
        if session.merchant_slug is None:
            return True
        if session.link is None:
            return session.merchant_slug == self._development
        current = await self._accounts.owner(session.merchant_slug)
        return current is not None and same(current.fingerprint, session.link)

    async def claim(self, session_id: str) -> str:
        """The owner cookie of the merchant the session just built, for the building browser.

        Once, within minutes of the build that attached the session in this process, while its
        link is current: `NotSignedIn` otherwise. The session then needs that cookie, like any
        signed-in one.
        """
        session = self._sessions.find(session_id)
        claimable = session is not None and (session.claimable_until or 0) > time.monotonic()
        if session is None or not claimable or not await self.acts_for_its_merchant(session):
            raise NotSignedIn(NOT_SIGNED_IN)
        session.claimable_until = None
        session.needs_cookie = True
        owner = await self._accounts.owner(session.merchant_slug or "")
        if owner is None:
            raise NotSignedIn(NOT_SIGNED_IN)
        return self._accounts.owner_cookie(owner)

    async def merchant(
        self, session_id: str | None, *, owner_cookie: str | None
    ) -> MerchantProfile | None:
        """The merchant the session works on, so that the web finds it after a reload.

        Read-only: an unknown session isn't created. It reports the owner's merchant, else
        PLATFORM_MERCHANT, like a new one, since its first message attaches it; so does a
        session whose link was replaced.
        """
        if self._profiles is None:
            return None
        owner = await self._accounts.owner_from_cookie(owner_cookie)
        session = self._sessions.find(session_id)
        if session is not None and not await self.acts_for_its_merchant(session):
            session = None
        default = owner.slug if owner else self._development
        return await session_merchant(session, default, self._profiles)
