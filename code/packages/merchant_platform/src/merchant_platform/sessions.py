"""In-memory merchant chat sessions (POC: one hard-coded merchant session, lost on restart)."""

import asyncio
from dataclasses import dataclass, field
from typing import Any

DEFAULT_SESSION = "default"


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
    # Held for the whole turn: one turn at a time per session.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # The running turn, kept referenced so it isn't garbage-collected mid-flight.
    turn: asyncio.Task[None] | None = None


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

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
