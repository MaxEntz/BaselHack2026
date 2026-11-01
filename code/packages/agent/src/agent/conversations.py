"""A merchant agent's conversations with guests' agents (#246), in memory like the platform's
chat sessions: a restart loses them, and the caller gets `not_found` and starts again.

A conversation belongs to one merchant and one caller (the BATP session's `agent.id`). It can't
be tied to the session itself: the gateway carries all its users on one session, under one
`agent.id`, so its random id is what keeps them apart (#245).
"""

import asyncio
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from agent.guest_tools import Proposal
from core.errors import NotFound
from core.llm import Message
from core.models import TalkOutput

# Since its last message: past it, the conversation is forgotten.
IDLE_SECONDS = 30 * 60
MAX_TURNS = 20
# Past it, the conversation used longest ago is forgotten.
MAX_CONVERSATIONS = 1000

UNKNOWN = "Unknown or expired conversation: start a new one, without `conversation`"
TOO_LONG = f"This conversation has had its {MAX_TURNS} messages: start a new one"


@dataclass(eq=False)
class Conversation:
    id: str
    slug: str
    caller: str
    used_at: float
    # In the LLM client's neutral format (`core.llm`).
    messages: list[Message] = field(default_factory=list)
    # The one write waiting for the guest's yes (#247): a new proposal replaces it.
    pending: Proposal | None = None
    # Each message's answer, by its `idempotency_key`: a replay gets it again, with no model
    # call and no write.
    answers: dict[str, TalkOutput] = field(default_factory=dict)
    turns: int = 0
    # Held for the whole turn: one message at a time, so a replay waits for the answer.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def take_turn(self) -> None:
        """Counts a new message (not a replay); `NotFound` past `MAX_TURNS`."""
        if self.turns >= MAX_TURNS:
            raise NotFound(TOO_LONG)
        self.turns += 1


class Conversations:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._conversations: dict[str, Conversation] = {}
        # The conversation each first message started: its replay has no id to send yet.
        self._first: dict[tuple[str, str, str], str] = {}

    def __len__(self) -> int:
        return len(self._conversations)

    def open(self, slug: str, caller: str, conversation_id: str | None, key: str) -> Conversation:
        """The conversation a message goes to: the one it names, else the one its key started
        (a replayed first message), else a new one. `NotFound` for an id that is unknown,
        expired, or another merchant's or caller's: the three look the same to the caller."""
        self._forget_idle()
        if conversation_id is not None:
            known = self._conversations.get(conversation_id)
            if known is None or (known.slug, known.caller) != (slug, caller):
                raise NotFound(UNKNOWN)
        else:
            known = self._conversations.get(self._first.get((slug, caller, key), ""))
            if known is None:
                known = self._start(slug, caller, key)
        known.used_at = self._clock()
        return known

    def _start(self, slug: str, caller: str, key: str) -> Conversation:
        while len(self._conversations) >= MAX_CONVERSATIONS:
            oldest = min(self._conversations.values(), key=lambda c: c.used_at)
            self._forget(oldest)
        conversation_id = f"c-{secrets.token_urlsafe(24)}"
        conversation = Conversation(conversation_id, slug, caller, self._clock())
        self._conversations[conversation_id] = conversation
        self._first[(slug, caller, key)] = conversation_id
        return conversation

    def _forget_idle(self) -> None:
        limit = self._clock() - IDLE_SECONDS
        for conversation in [c for c in self._conversations.values() if c.used_at < limit]:
            self._forget(conversation)

    def _forget(self, conversation: Conversation) -> None:
        del self._conversations[conversation.id]
        self._first = {key: cid for key, cid in self._first.items() if cid != conversation.id}
