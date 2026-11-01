"""The node's bounds on `TALK` (#249): turns at once, on the node and per merchant, and messages
a minute per merchant, checked before any model call."""

import asyncio

import pytest

from batp.status import TooManyRequests
from merchant_node.limits import (
    AT_ONCE,
    PER_MERCHANT_AT_ONCE,
    PER_MINUTE,
    WINDOW_SECONDS,
    TalkLimits,
)


class Clock:
    def __init__(self) -> None:
        self.seconds = 1000.0

    def __call__(self) -> float:
        return self.seconds


async def held(limits: TalkLimits, slug: str, release: asyncio.Event) -> None:
    async with limits.turn(slug):
        await release.wait()


async def started(limits: TalkLimits, slug: str, release: asyncio.Event) -> asyncio.Task[None]:
    """A turn that holds its slot until `release`."""
    task = asyncio.create_task(held(limits, slug, release))
    await asyncio.sleep(0)
    return task


def test_the_defaults() -> None:
    limits = TalkLimits()
    assert (AT_ONCE, PER_MERCHANT_AT_ONCE, PER_MINUTE, WINDOW_SECONDS) == (8, 3, 30, 60.0)
    assert (limits.at_once, limits.per_merchant_at_once, limits.per_minute) == (8, 3, 30)


async def test_the_minute_slides() -> None:
    clock = Clock()
    limits = TalkLimits(per_minute=2, clock=clock)
    async with limits.turn("zinc"):
        pass
    clock.seconds += 30
    async with limits.turn("zinc"):
        pass
    with pytest.raises(TooManyRequests, match="2 messages a minute"):
        async with limits.turn("zinc"):
            pass
    # Another merchant has its own minute.
    async with limits.turn("albane"):
        pass

    # The first message leaves the window 60 s after it was sent, the second 30 s later.
    clock.seconds += WINDOW_SECONDS - 30
    async with limits.turn("zinc"):
        pass
    with pytest.raises(TooManyRequests):
        async with limits.turn("zinc"):
            pass
    clock.seconds += 30
    async with limits.turn("zinc"):
        pass


async def test_a_refused_message_doesnt_count_in_the_minute() -> None:
    clock = Clock()
    limits = TalkLimits(per_minute=1, clock=clock)
    async with limits.turn("zinc"):
        pass
    for _ in range(5):
        with pytest.raises(TooManyRequests):
            async with limits.turn("zinc"):
                pass
    clock.seconds += WINDOW_SECONDS
    async with limits.turn("zinc"):
        pass


async def test_a_turn_frees_its_slot_however_it_ends() -> None:
    limits = TalkLimits(at_once=1)
    async with limits.turn("zinc"):
        assert limits.running == 1
    assert limits.running == 0

    with pytest.raises(RuntimeError):
        async with limits.turn("zinc"):
            raise RuntimeError("the model failed")
    assert limits.running == 0

    release = asyncio.Event()
    task = await started(limits, "zinc", release)
    assert limits.running == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert limits.running == 0
    # The slot is free again.
    async with limits.turn("zinc"):
        pass


async def test_turns_at_once_on_the_node() -> None:
    limits = TalkLimits(at_once=2, per_merchant_at_once=2)
    release = asyncio.Event()
    tasks = [await started(limits, slug, release) for slug in ("zinc", "albane")]
    with pytest.raises(TooManyRequests, match="busy"):
        async with limits.turn("graine"):
            pass
    release.set()
    await asyncio.gather(*tasks)
    async with limits.turn("graine"):
        pass


async def test_one_busy_merchant_leaves_the_others_answering() -> None:
    limits = TalkLimits(at_once=8, per_merchant_at_once=2)
    release = asyncio.Event()
    tasks = [await started(limits, "zinc", release) for _ in range(2)]
    with pytest.raises(TooManyRequests, match="other guests"):
        async with limits.turn("zinc"):
            pass
    async with limits.turn("albane"):
        pass
    release.set()
    await asyncio.gather(*tasks)
    async with limits.turn("zinc"):
        pass
    assert limits.running == 0
