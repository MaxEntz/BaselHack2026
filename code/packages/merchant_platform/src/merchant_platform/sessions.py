"""In-memory merchant chat sessions (POC: one hard-coded merchant session, lost on restart)."""

import asyncio
import secrets
from dataclasses import dataclass, field
from typing import Any

from core.models import (
    AssistantItem,
    CardItem,
    ChatEvent,
    ConversationItem,
    ErrorEvent,
    ErrorItem,
    TextEvent,
    UserItem,
)

DEFAULT_SESSION = "default"
# What the management site's panel gets back after a reload (#140): the last items only.
MAX_ITEMS = 200


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
