"""A merchant agent's conversations with guests' agents (#246), in memory like the platform's
chat sessions: a restart loses them, and the caller gets `not_found` and starts again.

A conversation belongs to one merchant and one caller (the BATP session's `agent.id`). It can't
be tied to the session itself: the gateway carries all its users on one session, under one
`agent.id`, so its random id is what keeps them apart (#245).

A replay is a message sent again with its key, and the node can't tell it from another guest
sending the same message with the same key. A caller that carries several guests must send keys
they can't guess, so that one guest never gets another's answer.
"""

import asyncio
import hashlib
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from agent.guest_tools import Proposal
from core.errors import InvalidInput, NotFound
from core.llm import Message
from core.models import TalkInput, TalkOutput

# Since its last message: past it, the conversation is forgotten.
IDLE_SECONDS = 30 * 60
MAX_TURNS = 20
# Past it, the conversation used longest ago is forgotten.
MAX_CONVERSATIONS = 1000

UNKNOWN = "Unknown or expired conversation: start a new one, without `conversation`"
TOO_LONG = f"This conversation has had its {MAX_TURNS} messages: start a new one"
REUSED_KEY = "This idempotency_key was sent with another message: use a new key per message"


def fingerprint(inp: TalkInput) -> str:
    """What a key was sent with: a replay must match it. The gateway carries all its guests under
    one `agent.id`, so a key alone, which any caller picks, can't tell their messages apart."""
    sent = [inp.message, inp.conversation, inp.confirm]
    return hashlib.sha256(json.dumps(sent).encode()).hexdigest()


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
    # Each message's answer, by its `idempotency_key`, with the message's `fingerprint`: a replay
    # gets it again, with no model call and no write; another message with that key, nothing.
    answers: dict[str, tuple[str, TalkOutput]] = field(default_factory=dict)
    turns: int = 0
    # Held for the whole turn: one message at a time, so a replay waits for the answer.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def answered(self, inp: TalkInput) -> TalkOutput | None:
        """The stored answer to this message; `InvalidInput` when its key came with another."""
        stored = self.answers.get(inp.idempotency_key)
        if stored is None:
            return None
        if stored[0] != fingerprint(inp):
            raise InvalidInput(REUSED_KEY)
        return stored[1]

    def keep(self, inp: TalkInput, output: TalkOutput) -> None:
        self.answers[inp.idempotency_key] = (fingerprint(inp), output)

    def take_turn(self) -> None:
        """Counts a new message (not a replay); `NotFound` past `MAX_TURNS`."""
        if self.turns >= MAX_TURNS:
            raise NotFound(TOO_LONG)
        self.turns += 1


class Conversations:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._conversations: dict[str, Conversation] = {}
        # The conversation each first message started, with the message's fingerprint: its
        # replay has no id to send yet.
        self._first: dict[tuple[str, str, str], tuple[str, str]] = {}

    def __len__(self) -> int:
        return len(self._conversations)

    def open(self, slug: str, caller: str, inp: TalkInput) -> Conversation:
        """The conversation a message goes to: the one it names, else the one its key started
        (a replayed first message), else a new one. `NotFound` for an id that is unknown,
        expired, or another merchant's or caller's: the three look the same to the caller.
        `InvalidInput` for a first message whose key started a conversation with another one:
        two guests behind one gateway must not land in the same conversation."""
        self._forget_idle()
        if inp.conversation is not None:
            known = self._conversations.get(inp.conversation)
            if known is None or (known.slug, known.caller) != (slug, caller):
                raise NotFound(UNKNOWN)
        else:
            started = self._first.get((slug, caller, inp.idempotency_key))
            known = self._conversations.get(started[0]) if started else None
            if known is None:
                known = self._start(slug, caller, inp)
            elif started and started[1] != fingerprint(inp):
                raise InvalidInput(REUSED_KEY)
        known.used_at = self._clock()
        return known

    def _start(self, slug: str, caller: str, inp: TalkInput) -> Conversation:
        while len(self._conversations) >= MAX_CONVERSATIONS:
            oldest = min(self._conversations.values(), key=lambda c: c.used_at)
            self._forget(oldest)
        conversation_id = f"c-{secrets.token_urlsafe(24)}"
        conversation = Conversation(conversation_id, slug, caller, self._clock())
        self._conversations[conversation_id] = conversation
        self._first[(slug, caller, inp.idempotency_key)] = (conversation_id, fingerprint(inp))
        return conversation

    def _forget_idle(self) -> None:
        limit = self._clock() - IDLE_SECONDS
        for conversation in [c for c in self._conversations.values() if c.used_at < limit]:
            self._forget(conversation)

    def _forget(self, conversation: Conversation) -> None:
        del self._conversations[conversation.id]
        self._first = {
            key: started for key, started in self._first.items() if started[0] != conversation.id
        }
