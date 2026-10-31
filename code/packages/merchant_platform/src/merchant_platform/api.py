"""FastAPI app: `/chat` (SSE), `/events` (SSE), `/session`, the public booking API, the
management site's `/merchants/{slug}` routes and `/health`."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, PositiveInt
from sqlalchemy.ext.asyncio import AsyncEngine
from sse_starlette import EventSourceResponse, ServerSentEvent

from agent.service import AgentService, PgAgentService
from core.config import Settings, get_settings
from core.db import make_engine
from core.errors import AgentError, AgentUnavailable, FormProblem
from core.models import (
    BookingTime,
    ChatEvent,
    DoneEvent,
    ErrorCode,
    ErrorEvent,
    GuestText,
    ManagementError,
    ManagementErrorCode,
    ReservationOutput,
    SessionMerchant,
    SessionOutput,
    Slug,
    ToolError,
)
from merchant_platform.assistant import (
    ProfileLookup,
    RunTurn,
    default_assistant,
    registry_profiles,
    run_turn,
    session_merchant,
)
from merchant_platform.auth import NotSignedIn, OtherMerchant
from merchant_platform.events import LiveBus, LiveListener
from merchant_platform.logs import configure_logging
from merchant_platform.management import NotBuiltYet
from merchant_platform.management import router as management_router
from merchant_platform.sessions import Session, SessionStore

log = logging.getLogger("merchant_platform.api")

MAX_MESSAGE_LENGTH = 4000
# SSE comment interval, so that proxies don't cut idle streams.
HEARTBEAT_SECONDS = 15.0
TURN_FAILED = "Something went wrong on our side. Please try again."

HTTP_STATUS: dict[ErrorCode, int] = {
    "not_found": 404,
    "slot_unavailable": 409,
    "party_too_large": 422,
    "invalid_input": 422,
    "agent_unavailable": 503,
}


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChatRequest(_Body):
    session_id: Annotated[str, Field(max_length=100)] | None = None
    message: Annotated[str, Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)]


class ResetSessionRequest(_Body):
    session_id: Annotated[str, Field(max_length=100)] | None = None


class PublicReservationInput(_Body):
    """The showcase site's booking form (the merchant comes from the URL)."""

    starts_at: BookingTime
    party_size: PositiveInt
    guest_name: GuestText
    idempotency_key: GuestText


def create_app(
    settings: Settings,
    *,
    assistant: RunTurn = run_turn,
    agent: AgentService | None = None,
    profiles: ProfileLookup | None = None,
    bus: LiveBus | None = None,
    listener: LiveListener | None = None,
    engine: AsyncEngine | None = None,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
) -> FastAPI:
    """Build the platform API around its collaborators (injected by tests).

    Without `agent`, the public booking API answers `agent_unavailable`. Without `profiles`
    (the registry), `/session` reports no merchant. `listener` feeds `bus` while the app
    runs; `engine` is disposed at shutdown.
    """
    sessions = SessionStore()
    live = bus or LiveBus()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        listening = asyncio.create_task(listener.run()) if listener else None
        try:
            yield
        finally:
            # Don't leave turns or the listener running past shutdown.
            tasks = [s.turn for s in sessions.all() if s.turn and not s.turn.done()]
            if listening:
                tasks.append(listening)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if engine:
                await engine.dispose()

    app = FastAPI(title="Sésame platform", lifespan=lifespan)
    app.state.sessions = sessions
    app.state.bus = live
    app.state.development_merchant = settings.platform_merchant
    app.state.agent = agent
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.public_cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["content-type"],
    )

    @app.exception_handler(AgentError)
    async def agent_error(_: Request, error: AgentError) -> JSONResponse:
        return tool_error(error.code, str(error))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
        # Field paths and reasons only: never echo the submitted values back.
        problems = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc'][1:])}: {issue['msg']}"
            for issue in error.errors()
        )
        return tool_error("invalid_input", problems or "Invalid request.")

    @app.exception_handler(FormProblem)
    async def form_problem(_: Request, error: FormProblem) -> JSONResponse:
        body = ManagementError(error=error.code, message=str(error), problem=error.problem)
        return JSONResponse(body.model_dump(mode="json", exclude_none=True), status_code=422)

    @app.exception_handler(NotSignedIn)
    async def not_signed_in(_: Request, error: NotSignedIn) -> JSONResponse:
        return management_error(401, "not_signed_in", str(error))

    @app.exception_handler(OtherMerchant)
    async def other_merchant(_: Request, error: OtherMerchant) -> JSONResponse:
        return management_error(403, "other_merchant", str(error))

    @app.exception_handler(NotBuiltYet)
    async def not_built_yet(_: Request, error: NotBuiltYet) -> JSONResponse:
        return management_error(501, "not_built_yet", str(error))

    app.include_router(management_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/chat", response_class=EventSourceResponse)
    async def chat(body: ChatRequest) -> EventSourceResponse:
        session = sessions.get(body.session_id)
        # No await between the check and the acquisition: no other request can slip in.
        if session.lock.locked():
            raise HTTPException(409, "A reply is already in progress for this session.")
        await session.lock.acquire()
        queue: asyncio.Queue[ChatEvent] = asyncio.Queue()
        # The turn runs as its own task, which releases the lock even if the client never
        # reads the stream.
        session.turn = asyncio.create_task(_run(assistant, session, body.message, queue))
        return EventSourceResponse(_chat_stream(queue, session.turn), ping=heartbeat_seconds)

    @app.get("/session")
    async def session_info(
        session_id: Annotated[str | None, Query(max_length=100)] = None,
    ) -> SessionOutput:
        """The merchant the chat session works on, so that the web finds it after a reload.

        Read-only: an unknown session isn't created, and reports PLATFORM_MERCHANT like a new
        one, since its first message attaches it.
        """
        if profiles is None:
            return SessionOutput(merchant=None)
        profile = await session_merchant(
            sessions.find(session_id), settings.platform_merchant, profiles
        )
        if profile is None:
            return SessionOutput(merchant=None)
        return SessionOutput(merchant=SessionMerchant(slug=profile.slug, name=profile.name))

    @app.get("/events", response_class=EventSourceResponse)
    async def events(merchant: Annotated[Slug, Query()]) -> EventSourceResponse:
        async def stream() -> AsyncIterator[ServerSentEvent]:
            # Subscribe inside the generator: its `finally` always pairs the unsubscribe.
            queue = live.subscribe(merchant)
            try:
                # Bytes right away, not at the first ping: a proxy (Vite's in dev) only sends
                # the headers with the first body bytes, so `EventSource` stays "connecting"
                # until then (#87).
                yield ServerSentEvent(comment="connected")
                while True:
                    event = await queue.get()
                    yield ServerSentEvent(data=event.model_dump_json(), event=event.type)
            finally:
                live.unsubscribe(merchant, queue)

        return EventSourceResponse(stream(), ping=heartbeat_seconds)

    @app.post("/public/{slug}/reservations", status_code=201)
    async def public_reservation(
        slug: Annotated[Slug, Path()], body: PublicReservationInput
    ) -> ReservationOutput:
        if agent is None:
            raise AgentUnavailable("Online booking is not available yet.")
        started = time.perf_counter()
        # `ok` only once booked: a crash or a cancelled request is never logged as `ok` (#79).
        outcome = "error"
        try:
            reservation = await agent.create_reservation(
                slug,
                body.starts_at,
                body.party_size,
                body.guest_name,
                body.idempotency_key,
                via="website",
            )
            outcome = "ok"
        except AgentError as error:
            outcome = error.code
            raise
        finally:
            log.info(
                "public_reservation",
                extra={"merchant": slug, "latency_ms": _since(started), "outcome": outcome},
            )
        return ReservationOutput(reservation=reservation)

    if settings.platform_dev_routes:

        @app.post("/dev/reset-session", status_code=204)
        async def reset_session(body: ResetSessionRequest) -> Response:
            """Restart onboarding without restarting the server."""
            if sessions.get(body.session_id).lock.locked():
                raise HTTPException(409, "Wait for the current reply to finish.")
            sessions.reset(body.session_id)
            return Response(status_code=204)

    return app


async def _run(
    assistant: RunTurn, session: Session, message: str, queue: asyncio.Queue[ChatEvent]
) -> None:
    """One assistant turn: events go to `queue`, which always ends with `done`."""

    async def emit(event: ChatEvent) -> None:
        # `_run` sends the only `done`, once the turn is over and the session is free: an
        # assistant still awaiting after its own `done` must not be cut off.
        if not isinstance(event, DoneEvent):
            queue.put_nowait(event)

    started = time.perf_counter()
    outcome = "ok"
    try:
        await assistant(session, message, emit)
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except Exception:
        outcome = "error"
        # The traceback goes to the logs, never to the merchant.
        log.exception("chat_turn_failed", extra={"session": session.id})
        await emit(ErrorEvent(message=TURN_FAILED))
    finally:
        session.lock.release()
        queue.put_nowait(DoneEvent())
        log.info(
            "chat_turn",
            extra={"session": session.id, "latency_ms": _since(started), "outcome": outcome},
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


def management_error(status: int, code: ManagementErrorCode, message: str) -> JSONResponse:
    body = ManagementError(error=code, message=message)
    return JSONResponse(body.model_dump(mode="json", exclude_none=True), status_code=status)


def tool_error(code: ErrorCode, message: str) -> JSONResponse:
    content = ToolError(error=code, message=message).model_dump(mode="json", exclude_none=True)
    return JSONResponse(content, status_code=HTTP_STATUS[code])


def _since(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


@lru_cache
def _default_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    engine = make_engine(settings.database_url)
    bus = LiveBus()
    listener = LiveListener(settings.database_url, engine, bus, settings.merchant_tz)
    agent = PgAgentService(engine, tz=ZoneInfo(settings.merchant_tz))
    return create_app(
        settings,
        assistant=default_assistant(settings, engine, agent),
        agent=agent,
        profiles=registry_profiles(engine),
        bus=bus,
        listener=listener,
        engine=engine,
    )


def __getattr__(name: str) -> Any:
    """`uvicorn merchant_platform.api:app`: build the app on first access, so that importing
    this module doesn't require DATABASE_URL."""
    if name == "app":
        return _default_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
