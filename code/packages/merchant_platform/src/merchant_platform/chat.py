"""The merchant's chat with the platform assistant: `POST /chat` (SSE), `GET /session`,
`POST /session/claim` and the dev-only `POST /dev/reset-session`.

Who a session acts for is `SessionGuard`'s (`sessions.py`); these routes read the cookies, run
one turn at a time per session and stream its events.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette import EventSourceResponse, ServerSentEvent

from core.models import (
    ChatEvent,
    DoneEvent,
    ErrorEvent,
    SessionMerchant,
    SessionOutput,
)
from merchant_platform.accounts import INVITE_COOKIE, OWNER_COOKIE
from merchant_platform.auth import set_owner_cookie
from merchant_platform.logs import session_tag, traceback_without
from merchant_platform.sessions import RunTurn, Session, SessionGuard, SessionStore

# The platform API's logger: these lines kept their name when the routes moved here.
log = logging.getLogger("merchant_platform.api")

MAX_MESSAGE_LENGTH = 4000
MAX_SESSION_ID = 100
TURN_FAILED = "Something went wrong on our side. Please try again."
# Not a `ToolError`: no §8.4 code says "busy", and the web only reads the status.
BUSY = "A reply is already in progress for this session."
WAIT = "Wait for the current reply to finish."

SessionId = Annotated[str, Field(min_length=1, max_length=MAX_SESSION_ID)]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChatRequest(_Body):
    session_id: SessionId | None = None
    message: Annotated[str, Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)]


class ClaimRequest(_Body):
    session_id: SessionId


class ResetSessionRequest(_Body):
    # Empty: the default session, as `null`.
    session_id: Annotated[str, Field(max_length=MAX_SESSION_ID)] | None = None


def _guard(request: Request) -> SessionGuard:
    guard: SessionGuard = request.app.state.guard
    return guard


def _sessions(request: Request) -> SessionStore:
    sessions: SessionStore = request.app.state.sessions
    return sessions


Guard = Annotated[SessionGuard, Depends(_guard)]
Sessions = Annotated[SessionStore, Depends(_sessions)]

router = APIRouter(tags=["chat"])


@router.post("/chat", response_class=EventSourceResponse)
async def chat(request: Request, body: ChatRequest, guard: Guard) -> EventSourceResponse:
    session = await guard.open(
        body.session_id,
        owner_cookie=request.cookies.get(OWNER_COOKIE),
        invite_cookie=request.cookies.get(INVITE_COOKIE),
    )
    # No await between the check and the acquisition: no other request can slip in.
    if session.lock.locked():
        raise HTTPException(409, BUSY)
    await session.lock.acquire()
    queue: asyncio.Queue[ChatEvent] = asyncio.Queue()
    assistant: RunTurn = request.app.state.assistant
    # The turn runs as its own task, which releases the lock even if the client never reads
    # the stream.
    session.turn = asyncio.create_task(_run(assistant, session, body.message, queue))
    heartbeat: float = request.app.state.heartbeat_seconds
    return EventSourceResponse(_chat_stream(queue, session.turn), ping=heartbeat)


@router.post("/session/claim", status_code=204)
async def claim(request: Request, body: ClaimRequest, guard: Guard) -> Response:
    """The building browser's owner cookie, so that its live events open at once."""
    cookie = await guard.claim(body.session_id)
    response = Response(status_code=204)
    set_owner_cookie(request, response, cookie)
    return response


@router.get("/session")
async def session_info(
    request: Request,
    guard: Guard,
    session_id: Annotated[str | None, Query(max_length=MAX_SESSION_ID)] = None,
) -> SessionOutput:
    """The merchant the chat session works on, so that the web finds it after a reload."""
    profile = await guard.merchant(session_id, owner_cookie=request.cookies.get(OWNER_COOKIE))
    if profile is None:
        return SessionOutput(merchant=None)
    return SessionOutput(merchant=SessionMerchant(slug=profile.slug, name=profile.name))


# Included only with PLATFORM_DEV_ROUTES.
dev_router = APIRouter(tags=["development"])


@dev_router.post("/dev/reset-session", status_code=204)
async def reset_session(body: ResetSessionRequest, sessions: Sessions) -> Response:
    """Restart onboarding without restarting the server."""
    if sessions.get(body.session_id).lock.locked():
        raise HTTPException(409, WAIT)
    sessions.reset(body.session_id)
    return Response(status_code=204)


async def _run(
    assistant: RunTurn, session: Session, message: str, queue: asyncio.Queue[ChatEvent]
) -> None:
    """One assistant turn: events go to `queue`, which always ends with `done`."""

    async def emit(event: ChatEvent) -> None:
        # `_run` sends the only `done`, once the turn is over and the session is free: an
        # assistant still awaiting after its own `done` must not be cut off.
        if not isinstance(event, DoneEvent):
            session.record(event)
            queue.put_nowait(event)

    session.said(message)

    started = time.perf_counter()
    outcome = "ok"
    try:
        await assistant(session, message, emit)
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except Exception as error:
        outcome = "error"
        # The traceback goes to the logs, never to the merchant; the session's id, not even
        # inside the exception's message (#220). `exc` is where `log.exception` puts it.
        log.error(
            "chat_turn_failed",
            extra={
                "session": session_tag(session.id),
                "exc": traceback_without(error, session.id),
            },
        )
        await emit(ErrorEvent(message=TURN_FAILED))
    finally:
        session.lock.release()
        queue.put_nowait(DoneEvent())
        log.info(
            "chat_turn",
            extra={
                "session": session_tag(session.id),
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "outcome": outcome,
            },
        )


async def _chat_stream(
    queue: asyncio.Queue[ChatEvent], turn: asyncio.Task[None]
) -> AsyncIterator[ServerSentEvent]:
    try:
        while True:
            event = await queue.get()
            yield ServerSentEvent(data=event.model_dump_json(), event=event.type)
            # `done` only comes from `_run`, after the turn: cancelling below means the
            # client really left.
            if isinstance(event, DoneEvent):
                return
    finally:
        # Client gone mid-turn: stop the assistant rather than run it for nobody.
        if not turn.done():
            turn.cancel()
