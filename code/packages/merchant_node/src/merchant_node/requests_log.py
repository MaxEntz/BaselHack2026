"""The node's request log: one row per answered request addressed to a merchant (#146).

The management site's Agent page reads it: who asked what, and the frames exchanged. `HELLO`,
and so the caller's token, is never stored: only the request and response frames are. Rows hold
guest names, so only the merchant's own session may read them (AGENTS.md §12); nothing here is
logged. Rows older than 7 days are purged as new ones are written.

The log must never delay an answer, not even another request's: it writes through its own small
engine (`log_engine`), so it never takes a connection the handlers need, and it drops a record,
with a warning, when too many are already waiting.
"""

import logging
from datetime import timedelta

from sqlalchemy import Text, delete, func, insert, literal, select
from sqlalchemy.ext.asyncio import AsyncEngine

from batp.codec import encode
from batp.messages import AgentIdentity, Request, Response
from core.db import agent_requests_t, make_engine, merchants_t
from core.models import PUBLISHED_STATUSES

log = logging.getLogger("merchant_node.requests_log")

KEEP = timedelta(days=7)
# Records waiting for the log's connection beyond this are dropped (a burst, or Postgres slow).
MAX_PENDING = 100


def log_engine(url: str | None = None) -> AsyncEngine:
    """The log's own engine: at most 2 connections, apart from the handlers' pool."""
    return make_engine(url, pool_size=1, max_overflow=1)


class RequestLog:
    """`NodeServer`'s recorder. Runs after the answer, in its own task (`NodeServer.record`)."""

    def __init__(
        self, engine: AsyncEngine, *, keep: timedelta = KEEP, max_pending: int = MAX_PENDING
    ) -> None:
        self.engine = engine
        self.keep = keep
        self.max_pending = max_pending
        self._pending = 0

    async def __call__(self, agent: AgentIdentity, request: Request, response: Response) -> None:
        if request.merchant is None:
            return  # DISCOVER, RECALL, PING: no merchant's page to show them on
        if self._pending >= self.max_pending:
            log.warning("request log full: record dropped", extra={"intent": request.intent})
            return
        self._pending += 1
        try:
            await self._write(agent, request, response)
        finally:
            self._pending -= 1

    async def _write(self, agent: AgentIdentity, request: Request, response: Response) -> None:
        row = select(
            merchants_t.c.slug,
            literal(agent.name),
            literal(request.intent),
            literal(response.status),
            literal(_frame(request), Text),
            literal(_frame(response), Text),
        ).where(
            merchants_t.c.slug == request.merchant,
            # Published agents only: what an Agent page shows (`building` ones answer 404).
            merchants_t.c.status.in_(PUBLISHED_STATUSES),
        )
        columns = ["merchant_slug", "agent_name", "intent", "status", "request", "response"]
        async with self.engine.begin() as conn:
            # Selected from the registry: an unknown or unpublished merchant leaves no row.
            await conn.execute(insert(agent_requests_t).from_select(columns, row))
            await conn.execute(
                delete(agent_requests_t).where(agent_requests_t.c.at < func.now() - self.keep)
            )


def _frame(message: Request | Response) -> str:
    """The frame's line, without its LF: the response exactly as sent, the request as the node
    read it (`batp.codec.encode`: compact, unknown fields left out)."""
    return encode(message).decode().removesuffix("\n")
