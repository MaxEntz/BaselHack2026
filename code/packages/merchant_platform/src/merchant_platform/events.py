"""Postgres LISTEN/NOTIFY -> SSE live events for the merchant chat."""

import asyncio
from collections import defaultdict

from core.models import LiveEvent


class LiveBus:
    """Fans live events out to the `/events` streams open for each merchant."""

    def __init__(self) -> None:
        self._subscribers: defaultdict[str, set[asyncio.Queue[LiveEvent]]] = defaultdict(set)

    def subscribe(self, slug: str) -> asyncio.Queue[LiveEvent]:
        queue: asyncio.Queue[LiveEvent] = asyncio.Queue()
        self._subscribers[slug].add(queue)
        return queue

    def unsubscribe(self, slug: str, queue: asyncio.Queue[LiveEvent]) -> None:
        subscribers = self._subscribers.get(slug)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            del self._subscribers[slug]

    def subscriber_count(self, slug: str) -> int:
        return len(self._subscribers.get(slug, ()))

    def publish(self, slug: str, event: LiveEvent) -> None:
        for queue in self._subscribers.get(slug, ()):
            queue.put_nowait(event)
