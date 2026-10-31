"""The node's request log: one row per answered request addressed to a merchant (#146).

The management site's Agent page reads it: who asked what, and the frames exchanged. `HELLO`,
and so the caller's token, is never stored: only the request and response frames are. Rows hold
guest names, so only the merchant's own session may read them (AGENTS.md §12); nothing here is
logged. Rows older than 7 days are purged as new ones are written.
"""

from datetime import timedelta

from sqlalchemy import delete, func, insert, literal, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine

from batp.messages import AgentIdentity, Request, Response
from core.db import agent_requests_t, merchants_t

KEEP = timedelta(days=7)


class RequestLog:
    """`NodeServer`'s recorder. Runs after the answer, in its own task (`NodeServer.record`)."""

    def __init__(self, engine: AsyncEngine, *, keep: timedelta = KEEP) -> None:
        self.engine = engine
        self.keep = keep

    async def __call__(self, agent: AgentIdentity, request: Request, response: Response) -> None:
        if request.merchant is None:
            return  # DISCOVER, RECALL, PING: no merchant's page to show them on
        row = select(
            merchants_t.c.slug,
            literal(agent.name),
            literal(request.intent),
            literal(response.status),
            literal(_frame(request), JSONB),
            literal(_frame(response), JSONB),
        ).where(merchants_t.c.slug == request.merchant)
        columns = ["merchant_slug", "agent_name", "intent", "status", "request", "response"]
        async with self.engine.begin() as conn:
            # Selected from the registry: a request for an unknown merchant leaves no row.
            await conn.execute(insert(agent_requests_t).from_select(columns, row))
            await conn.execute(
                delete(agent_requests_t).where(agent_requests_t.c.at < func.now() - self.keep)
            )


def _frame(message: Request | Response) -> dict[str, object]:
    """The frame as it was on the wire (`batp.codec.encode`)."""
    return message.model_dump(mode="json", exclude_none=True)
