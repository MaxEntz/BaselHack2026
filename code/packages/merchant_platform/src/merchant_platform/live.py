"""`GET /events?merchant=`: a merchant's live events as SSE (`events.py` feeds the bus)."""

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Query, Request
from sse_starlette import EventSourceResponse, ServerSentEvent

from core.models import Slug
from merchant_platform.auth import request_owner, require_owner
from merchant_platform.events import LiveBus

router = APIRouter(tags=["live"])


@router.get("/events", response_class=EventSourceResponse)
async def events(request: Request, merchant: Annotated[Slug, Query()]) -> EventSourceResponse:
    # Its events carry guest names: the owner's only (#132).
    await require_owner(request, merchant)
    heartbeat: float = request.app.state.heartbeat_seconds
    return EventSourceResponse(_stream(request, merchant, heartbeat), ping=heartbeat)


async def _stream(
    request: Request, merchant: str, heartbeat_seconds: float
) -> AsyncIterator[ServerSentEvent]:
    bus: LiveBus = request.app.state.bus
    # Subscribe inside the generator: its `finally` always pairs the unsubscribe.
    queue = bus.subscribe(merchant)
    try:
        # Bytes right away, not at the first ping: a proxy (Vite's in dev) only sends the
        # headers with the first body bytes, so `EventSource` stays "connecting" until then (#87).
        yield ServerSentEvent(comment="connected")
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), heartbeat_seconds)
            except TimeoutError:
                event = None
            # A new link or a deleted merchant ends an open stream too (#132).
            if not await _still_owner(request, merchant):
                return
            if event is not None:
                yield ServerSentEvent(data=event.model_dump_json(), event=event.type)
    finally:
        bus.unsubscribe(merchant, queue)


async def _still_owner(request: Request, merchant: str) -> bool:
    owner = await request_owner(request)
    return owner is not None and owner.slug == merchant
