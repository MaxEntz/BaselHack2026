"""The merchant agents' conversations (#246): bound to a merchant and a caller, idle for 30
minutes at most, 20 messages each, 1000 kept."""

import pytest

from agent.conversations import IDLE_SECONDS, MAX_CONVERSATIONS, MAX_TURNS, Conversations
from core.errors import NotFound


class Clock:
    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds


def test_a_conversation_continues_until_it_is_idle_too_long() -> None:
    clock = Clock()
    store = Conversations(clock=clock)
    first = store.open("zinc", "agent-a", None, "k-1")
    clock.seconds = IDLE_SECONDS - 1
    assert store.open("zinc", "agent-a", first.id, "k-2") is first
    # Each message restarts the 30 minutes.
    clock.seconds += IDLE_SECONDS - 1
    assert store.open("zinc", "agent-a", first.id, "k-3") is first
    clock.seconds += IDLE_SECONDS + 1
    with pytest.raises(NotFound):
        store.open("zinc", "agent-a", first.id, "k-4")


def test_a_first_message_replayed_finds_the_conversation_it_started() -> None:
    store = Conversations(clock=Clock())
    first = store.open("zinc", "agent-a", None, "k-1")
    assert store.open("zinc", "agent-a", None, "k-1") is first
    assert store.open("zinc", "agent-a", None, "k-2") is not first
    assert store.open("zinc", "agent-b", None, "k-1") is not first


def test_ids_are_random_and_long() -> None:
    store = Conversations(clock=Clock())
    ids = {store.open("zinc", "agent-a", None, f"k-{n}").id for n in range(50)}
    assert len(ids) == 50
    # `c-` and 24 random bytes: 192 bits.
    assert all(len(cid) == 34 for cid in ids)


def test_a_conversation_takes_twenty_messages() -> None:
    conversation = Conversations(clock=Clock()).open("zinc", "agent-a", None, "k-1")
    for _ in range(MAX_TURNS):
        conversation.take_turn()
    with pytest.raises(NotFound, match="start a new one"):
        conversation.take_turn()


def test_past_the_limit_the_conversation_used_longest_ago_goes() -> None:
    clock = Clock()
    store = Conversations(clock=clock)
    oldest = store.open("zinc", "agent-a", None, "k-oldest")
    for n in range(MAX_CONVERSATIONS - 1):
        clock.seconds += 0.001
        store.open("zinc", "agent-a", None, f"k-{n}")
    assert len(store) == MAX_CONVERSATIONS
    store.open("zinc", "agent-a", None, "k-new")

    assert len(store) == MAX_CONVERSATIONS
    with pytest.raises(NotFound):
        store.open("zinc", "agent-a", oldest.id, "k-again")
