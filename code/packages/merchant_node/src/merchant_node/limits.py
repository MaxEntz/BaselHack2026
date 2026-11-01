"""Bounds on the merchants' AI agents (#249): each `TALK` is a model turn on the shared OpenAI
key, so the node caps them before any model call (PROTOCOL.md §10).

Past a limit the request is not carried out: `429 too_many_requests`, and the caller may send it
again later with the same key.
"""

import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from batp.status import TooManyRequests

# Model turns running at once on the node, every merchant together.
AT_ONCE = 8
# Model turns running at once for one merchant: one busy merchant leaves the others answering.
PER_MERCHANT_AT_ONCE = 3
# `TALK` requests to one merchant within a minute.
PER_MINUTE = 30
WINDOW_SECONDS = 60.0


class TalkLimits:
    def __init__(
        self,
        *,
        at_once: int = AT_ONCE,
        per_merchant_at_once: int = PER_MERCHANT_AT_ONCE,
        per_minute: int = PER_MINUTE,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.at_once = at_once
        self.per_merchant_at_once = per_merchant_at_once
        self.per_minute = per_minute
        self._clock = clock
        self._running = 0
        self._running_for: defaultdict[str, int] = defaultdict(int)
        # Per merchant, the times of its last requests: slugs the node routes, so few.
        self._recent: defaultdict[str, deque[float]] = defaultdict(deque)

    @asynccontextmanager
    async def turn(self, slug: str) -> AsyncIterator[None]:
        """One `TALK` to `slug`; `TooManyRequests` before it starts when a limit is reached."""
        now = self._clock()
        recent = self._recent[slug]
        while recent and recent[0] <= now - WINDOW_SECONDS:
            recent.popleft()
        if len(recent) >= self.per_minute:
            raise TooManyRequests(f"More than {self.per_minute} messages a minute to this agent")
        if self._running >= self.at_once:
            raise TooManyRequests("The merchants' agents are busy; try again in a moment")
        if self._running_for[slug] >= self.per_merchant_at_once:
            raise TooManyRequests("This agent is busy with other guests; try again in a moment")
        recent.append(now)
        self._running += 1
        self._running_for[slug] += 1
        try:
            yield
        finally:
            self._running -= 1
            self._running_for[slug] -= 1
            if not self._running_for[slug]:
                del self._running_for[slug]

    @property
    def running(self) -> int:
        """Model turns running now, every merchant together."""
        return self._running
