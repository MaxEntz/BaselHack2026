"""The merchant agents' conversations (#246): bound to a merchant and a caller, idle for 30
minutes at most, 20 messages each, 1000 kept."""

import pytest

from agent.conversations import IDLE_SECONDS, MAX_CONVERSATIONS, MAX_TURNS, Conversations
from core.errors import InvalidInput, NotFound
from core.models import TalkInput, TalkOutput


def sent(key: str, conversation: str | None = None, text: str = "Table for 2?") -> TalkInput:
    return TalkInput(merchant="zinc", message=text, idempotency_key=key, conversation=conversation)


class Clock:
    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds


def test_a_conversation_continues_until_it_is_idle_too_long() -> None:
    clock = Clock()
    store = Conversations(clock=clock)
    first = store.open("zinc", "agent-a", sent("k-1"))
    clock.seconds = IDLE_SECONDS - 1
    assert store.open("zinc", "agent-a", sent("k-2", first.id)) is first
    # Each message restarts the 30 minutes.
    clock.seconds += IDLE_SECONDS - 1
    assert store.open("zinc", "agent-a", sent("k-3", first.id)) is first
    clock.seconds += IDLE_SECONDS + 1
    with pytest.raises(NotFound):
        store.open("zinc", "agent-a", sent("k-4", first.id))


def test_a_first_message_replayed_finds_the_conversation_it_started() -> None:
    store = Conversations(clock=Clock())
    first = store.open("zinc", "agent-a", sent("k-1"))
    assert store.open("zinc", "agent-a", sent("k-1")) is first
    assert store.open("zinc", "agent-a", sent("k-2")) is not first
    assert store.open("zinc", "agent-b", sent("k-1")) is not first


def test_ids_are_random_and_long() -> None:
    store = Conversations(clock=Clock())
    ids = {store.open("zinc", "agent-a", sent(f"k-{n}")).id for n in range(50)}
    assert len(ids) == 50
    # `c-` and 24 random bytes: 192 bits.
    assert all(len(cid) == 34 for cid in ids)


def test_a_conversation_takes_twenty_messages() -> None:
    conversation = Conversations(clock=Clock()).open("zinc", "agent-a", sent("k-1"))
    for _ in range(MAX_TURNS):
        conversation.take_turn()
    with pytest.raises(NotFound, match="start a new one"):
        conversation.take_turn()


def test_past_the_limit_the_conversation_used_longest_ago_goes() -> None:
    clock = Clock()
    store = Conversations(clock=clock)
    oldest = store.open("zinc", "agent-a", sent("k-oldest"))
    for n in range(MAX_CONVERSATIONS - 1):
        clock.seconds += 0.001
        store.open("zinc", "agent-a", sent(f"k-{n}"))
    assert len(store) == MAX_CONVERSATIONS
    store.open("zinc", "agent-a", sent("k-new"))

    assert len(store) == MAX_CONVERSATIONS
    with pytest.raises(NotFound):
        store.open("zinc", "agent-a", sent("k-again", oldest.id))


def test_a_key_sent_with_another_first_message_is_refused() -> None:
    # Two guests behind one gateway share its `agent.id`: a key alone can't tell them apart.
    store = Conversations(clock=Clock())
    store.open("zinc", "agent-a", sent("talk-1", text="Table for 2 tonight?"))
    with pytest.raises(InvalidInput, match="another message"):
        store.open("zinc", "agent-a", sent("talk-1", text="Table for 6 on Friday?"))


def test_a_key_reused_in_a_conversation_for_another_message_is_refused() -> None:
    store = Conversations(clock=Clock())
    first = sent("k-1")
    conversation = store.open("zinc", "agent-a", first)
    output = TalkOutput(conversation=conversation.id, reply="Yes.")
    conversation.keep(first, output)
    later = sent("k-2", conversation.id, "At 20:00")
    conversation.keep(later, output)

    assert conversation.answered(first) == output
    assert conversation.answered(later) == output
    with pytest.raises(InvalidInput, match="another message"):
        conversation.answered(sent("k-2", conversation.id, "At 21:00"))
