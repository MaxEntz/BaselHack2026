"""FastAPI app: `/chat` (SSE), `/events` (SSE), `/session`, the public booking API, the
management site's `/merchants/{slug}` routes, the owner's `/login` (#132) and `/health`."""

import asyncio
import logging
import secrets
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

from agent.management import ManagementService
from agent.service import AgentService, PgAgentService
from builder.build import Crawl
from builder.llm import LLMClient
from core.config import Settings, get_settings
from core.db import make_engine
from core.errors import AgentError, AgentUnavailable, FormProblem, InvalidInput
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
from merchant_platform.accounts import (
    INVITE_COOKIE,
    Accounts,
    MemoryOwnerStore,
    Owner,
    PgOwnerStore,
    Signer,
    same,
)
from merchant_platform.assistant import (
    ProfileLookup,
    RunTurn,
    default_assistant,
    registry_profiles,
    run_turn,
    session_merchant,
)
from merchant_platform.auth import (
    NOT_SIGNED_IN,
    OTHER_MERCHANT,
    NeedsInvite,
    NotSignedIn,
    OtherMerchant,
    cookie_owner,
    request_owner,
    require_owner,
    set_owner_cookie,
)
from merchant_platform.auth import router as auth_router
from merchant_platform.events import LiveBus, LiveListener
from merchant_platform.logs import configure_logging, session_tag
from merchant_platform.management import NotBuiltYet
from merchant_platform.management import router as management_router
from merchant_platform.overview import RegistryLookup, registry_records
from merchant_platform.sessions import DEFAULT_SESSION, Session, SessionStore

log = logging.getLogger("merchant_platform.api")

MAX_MESSAGE_LENGTH = 4000
# SSE comment interval, so that proxies don't cut idle streams.
HEARTBEAT_SECONDS = 15.0
TURN_FAILED = "Something went wrong on our side. Please try again."
NEEDS_INVITE = "Building a new agent needs an invitation: open the link you were given."

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
    session_id: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    message: Annotated[str, Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)]


class ClaimRequest(_Body):
    session_id: Annotated[str, Field(min_length=1, max_length=100)]


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
    accounts: Accounts | None = None,
    registry: RegistryLookup | None = None,
    bookings: ManagementService | None = None,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
) -> FastAPI:
    """Build the platform API around its collaborators (injected by tests).

    Without `agent`, the public booking API answers `agent_unavailable`. Without `profiles`
    (the registry), `/session` reports no merchant. `listener` feeds `bus` while the app
    runs; `engine` is disposed at shutdown. Without `accounts`, owners live in memory. Without
    `registry`, `GET /merchants/{slug}` answers `agent_unavailable`, and without `bookings`
    the management site's booking routes do.
    """
    check_account_settings(settings)
    sessions = SessionStore()
    live = bus or LiveBus()
    owners = accounts or default_accounts(settings, MemoryOwnerStore())

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
    app.state.accounts = owners
    app.state.registry = registry
    app.state.bookings = bookings
    app.state.settings = settings
    app.state.development_merchant = settings.platform_merchant
    app.state.secure_cookies = settings.platform_web_url.startswith("https://")
    app.state.agent = agent
    app.state.engine = engine
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

    @app.exception_handler(NeedsInvite)
    async def needs_invite(_: Request, error: NeedsInvite) -> JSONResponse:
        return management_error(403, "needs_invite", str(error))

    @app.exception_handler(NotBuiltYet)
    async def not_built_yet(_: Request, error: NotBuiltYet) -> JSONResponse:
        return management_error(501, "not_built_yet", str(error))

    app.include_router(auth_router)
    app.include_router(management_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/chat", response_class=EventSourceResponse)
    async def chat(request: Request, body: ChatRequest) -> EventSourceResponse:
        if body.session_id in (None, DEFAULT_SESSION) and settings.platform_merchant is None:
            # The shared default session is the development merchant's only (#132).
            raise InvalidInput("session_id is required")
        owner = await cookie_owner(request)
        invited = owners.invited(request.cookies.get(INVITE_COOKIE))
        known = sessions.find(body.session_id)
        if known is None and owner is None and not invited and not await _attached(None):
            # Before any model call, and before keeping a session for an anonymous caller.
            raise NeedsInvite(NEEDS_INVITE)
        session = sessions.get(body.session_id)
        await _bind(session, owner)
        if not invited and not await _attached(session):
            # An onboarding crawls a site and spends the OpenAI key.
            raise NeedsInvite(NEEDS_INVITE)
        # No await between the check and the acquisition: no other request can slip in.
        if session.lock.locked():
            raise HTTPException(409, "A reply is already in progress for this session.")
        await session.lock.acquire()
        queue: asyncio.Queue[ChatEvent] = asyncio.Queue()
        # The turn runs as its own task, which releases the lock even if the client never
        # reads the stream.
        session.turn = asyncio.create_task(_run(assistant, session, body.message, queue))
        return EventSourceResponse(_chat_stream(queue, session.turn), ping=heartbeat_seconds)

    async def _attached(session: Session | None) -> bool:
        """Whether the session works on a merchant, or will from its next message."""
        if session is not None and session.merchant_slug is not None:
            return True
        if profiles is None:
            return False
        return await session_merchant(session, settings.platform_merchant, profiles) is not None

    async def _acts_for_its_merchant(session: Session) -> bool:
        """The session's link is still its merchant's current one (#132). The development
        merchant's sessions carry none."""
        if session.merchant_slug is None:
            return True
        if session.link is None:
            return session.merchant_slug == settings.platform_merchant
        current = await owners.owner(session.merchant_slug)
        return current is not None and same(current.fingerprint, session.link)

    async def _bind(session: Session, owner: Owner | None) -> None:
        """Who the session acts for, checked on every message (#132).

        A cookie attaches it, or renews its link; another merchant's cookie is refused. A
        session whose link was replaced, or whose merchant was deleted, is forgotten with its
        history (it holds guest names) and must sign in again.
        """
        if owner is not None:
            if session.merchant_slug not in (None, owner.slug):
                raise OtherMerchant(OTHER_MERCHANT)
            session.merchant_slug, session.link = owner.slug, owner.fingerprint
            session.needs_cookie = True
            return
        if session.needs_cookie:
            raise NotSignedIn(NOT_SIGNED_IN)
        if not await _acts_for_its_merchant(session):
            # A running turn holds the old session: stop it with the session.
            if session.turn and not session.turn.done():
                session.turn.cancel()
            sessions.reset(session.id)
            raise NotSignedIn(NOT_SIGNED_IN)

    @app.post("/session/claim", status_code=204)
    async def claim(request: Request, body: ClaimRequest) -> Response:
        """The building browser's owner cookie, so that its live events open at once.

        Once, within minutes of the build that attached the session in this process, while its
        link is current. The session then needs that cookie, like any signed-in one.
        """
        session = sessions.find(body.session_id)
        claimable = session is not None and (session.claimable_until or 0) > time.monotonic()
        if session is None or not claimable or not await _acts_for_its_merchant(session):
            raise NotSignedIn(NOT_SIGNED_IN)
        session.claimable_until = None
        session.needs_cookie = True
        owner = await owners.owner(session.merchant_slug or "")
        if owner is None:
            raise NotSignedIn(NOT_SIGNED_IN)
        response = Response(status_code=204)
        set_owner_cookie(request, response, owners.owner_cookie(owner))
        return response

    @app.get("/session")
    async def session_info(
        request: Request,
        session_id: Annotated[str | None, Query(max_length=100)] = None,
    ) -> SessionOutput:
        """The merchant the chat session works on, so that the web finds it after a reload.

        Read-only: an unknown session isn't created. It reports the owner's merchant, else
        PLATFORM_MERCHANT, like a new one, since its first message attaches it.
        """
        if profiles is None:
            return SessionOutput(merchant=None)
        owner = await cookie_owner(request)
        session = sessions.find(session_id)
        if session is not None and not await _acts_for_its_merchant(session):
            session = None  # its link was replaced: it reports what a new session would
        default = owner.slug if owner else settings.platform_merchant
        profile = await session_merchant(session, default, profiles)
        if profile is None:
            return SessionOutput(merchant=None)
        return SessionOutput(merchant=SessionMerchant(slug=profile.slug, name=profile.name))

    @app.get("/events", response_class=EventSourceResponse)
    async def events(request: Request, merchant: Annotated[Slug, Query()]) -> EventSourceResponse:
        # Its events carry guest names: the owner's only (#132).
        await require_owner(request, merchant)

        async def still_owner() -> bool:
            owner = await request_owner(request)
            return owner is not None and owner.slug == merchant

        async def stream() -> AsyncIterator[ServerSentEvent]:
            # Subscribe inside the generator: its `finally` always pairs the unsubscribe.
            queue = live.subscribe(merchant)
            try:
                # Bytes right away, not at the first ping: a proxy (Vite's in dev) only sends
                # the headers with the first body bytes, so `EventSource` stays "connecting"
                # until then (#87).
                yield ServerSentEvent(comment="connected")
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), heartbeat_seconds)
                    except TimeoutError:
                        event = None
                    # A new link or a deleted merchant ends an open stream too (#132).
                    if not await still_owner():
                        return
                    if event is not None:
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
    except Exception:
        outcome = "error"
        # The traceback goes to the logs, never to the merchant.
        log.exception("chat_turn_failed", extra={"session": session_tag(session.id)})
        await emit(ErrorEvent(message=TURN_FAILED))
    finally:
        session.lock.release()
        queue.put_nowait(DoneEvent())
        log.info(
            "chat_turn",
            extra={
                "session": session_tag(session.id),
                "latency_ms": _since(started),
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


# Long enough not to be guessed online (POST /invite has no throttle) or offline (the key).
MIN_INVITE_CODE = 8
MIN_COOKIE_SECRET = 32


def check_account_settings(settings: Settings) -> None:
    """Refuse a platform that would open guest data or take a weak secret (#132).

    PLATFORM_MERCHANT opens its merchant to anyone, and PLATFORM_DEV_ROUTES an unauthenticated
    `/dev/reset-session`: both are for local development, so neither can come with what only a
    deployment sets (an https web app, a cookie key, an invite code). `make deploy` refuses
    them too (`scripts/platform_env.py`); this check holds wherever the platform starts (#220).
    """
    secret = settings.platform_cookie_secret
    invite = settings.platform_invite_code
    deployed = settings.platform_web_url.startswith("https://") or secret or invite
    if settings.platform_merchant and deployed:
        raise RuntimeError(
            "PLATFORM_MERCHANT is for local development only: unset it, or unset "
            "PLATFORM_COOKIE_SECRET, PLATFORM_INVITE_CODE and the https PLATFORM_WEB_URL"
        )
    if settings.platform_dev_routes and deployed:
        raise RuntimeError(
            "PLATFORM_DEV_ROUTES is for local development only: set it to 0, or unset "
            "PLATFORM_COOKIE_SECRET, PLATFORM_INVITE_CODE and the https PLATFORM_WEB_URL"
        )
    if secret is not None and len(secret.get_secret_value()) < MIN_COOKIE_SECRET:
        raise RuntimeError(f"PLATFORM_COOKIE_SECRET needs at least {MIN_COOKIE_SECRET} characters")
    if invite is not None and len(invite.get_secret_value()) < MIN_INVITE_CODE:
        raise RuntimeError(f"PLATFORM_INVITE_CODE needs at least {MIN_INVITE_CODE} characters")
    if secret is not None and not settings.platform_web_url.startswith("https://"):
        log.warning("PLATFORM_WEB_URL is not https: owner cookies are sent without Secure")


def default_accounts(settings: Settings, store: MemoryOwnerStore | PgOwnerStore) -> Accounts:
    if settings.platform_cookie_secret is None:
        secret = secrets.token_bytes(32)
        log.warning("PLATFORM_COOKIE_SECRET unset: owner cookies end with this process")
    else:
        secret = settings.platform_cookie_secret.get_secret_value().encode()
    invite = settings.platform_invite_code
    return Accounts(store, Signer(secret), invite.get_secret_value() if invite else None)


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
    return default_app(get_settings())


def default_app(
    settings: Settings, *, llm: LLMClient | None = None, crawler: Crawl | None = None
) -> FastAPI:
    """The platform as `make platform` runs it. `llm` and `crawler` stand in for OpenAI and
    the builder's crawl in the browser test of the whole stack (#153)."""
    engine = make_engine(settings.database_url)
    bus = LiveBus()
    listener = LiveListener(settings.database_url, engine, bus, settings.merchant_tz)
    agent = PgAgentService(engine, tz=ZoneInfo(settings.merchant_tz))
    accounts = default_accounts(settings, PgOwnerStore(engine))
    return create_app(
        settings,
        assistant=default_assistant(settings, engine, agent, accounts, llm=llm, crawler=crawler),
        agent=agent,
        profiles=registry_profiles(engine),
        bus=bus,
        listener=listener,
        engine=engine,
        accounts=accounts,
        registry=registry_records(engine),
        bookings=ManagementService(engine, agent, tz=ZoneInfo(settings.merchant_tz)),
    )


def __getattr__(name: str) -> Any:
    """`uvicorn merchant_platform.api:app`: build the app on first access, so that importing
    this module doesn't require DATABASE_URL."""
    if name == "app":
        return _default_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
