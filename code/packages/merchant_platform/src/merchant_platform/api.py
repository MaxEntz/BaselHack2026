"""FastAPI app: composes the platform's routers around its collaborators.

The routes: `chat.py` (`/chat`, `/session`, `/session/claim`, `/dev/reset-session`), `live.py`
(`/events`), `public.py` (`/health`, the public booking API), `auth.py` (the owner's `/login`,
#132) and `management.py` (the management site's `/merchants/{slug}`). `errors.py` answers
their errors.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.management import ManagementService
from agent.service import AgentService, PgAgentService
from builder.build import Crawl
from builder.llm import LLMClient
from core.config import Settings, get_settings
from core.db import make_engine
from merchant_platform import auth, chat, live, management, public
from merchant_platform.account_settings import check_account_settings, default_accounts
from merchant_platform.accounts import Accounts, MemoryOwnerStore, PgOwnerStore
from merchant_platform.assistant import default_assistant, run_turn
from merchant_platform.errors import add_error_handlers
from merchant_platform.events import LiveBus, LiveListener
from merchant_platform.logs import configure_logging
from merchant_platform.registry import (
    ProfileLookup,
    RegistryLookup,
    registry_profiles,
    registry_records,
)
from merchant_platform.sessions import RunTurn, SessionGuard, SessionStore

# SSE comment interval, so that proxies don't cut idle streams.
HEARTBEAT_SECONDS = 15.0


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
    owners = accounts or default_accounts(settings, MemoryOwnerStore())
    app = FastAPI(title="Sésame platform", lifespan=_lifespan(sessions, listener, engine))
    # What the routers read from the request (`auth.py`, `management.py`, `chat.py`…).
    app.state.settings = settings
    app.state.sessions = sessions
    app.state.guard = SessionGuard(sessions, owners, profiles, settings.platform_merchant)
    app.state.assistant = assistant
    app.state.heartbeat_seconds = heartbeat_seconds
    app.state.bus = bus or LiveBus()
    app.state.accounts = owners
    app.state.development_merchant = settings.platform_merchant
    app.state.secure_cookies = settings.platform_web_url.startswith("https://")
    app.state.agent = agent
    app.state.engine = engine
    app.state.registry = registry
    app.state.bookings = bookings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.public_cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["content-type"],
    )
    add_error_handlers(app)
    for router in (auth.router, management.router, public.router, chat.router, live.router):
        app.include_router(router)
    if settings.platform_dev_routes:
        app.include_router(chat.dev_router)
    return app


def _lifespan(
    sessions: SessionStore, listener: LiveListener | None, engine: AsyncEngine | None
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
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

    return lifespan


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
    tz = ZoneInfo(settings.merchant_tz)
    bus = LiveBus()
    agent = PgAgentService(engine, tz=tz)
    accounts = default_accounts(settings, PgOwnerStore(engine))
    return create_app(
        settings,
        assistant=default_assistant(settings, engine, agent, accounts, llm=llm, crawler=crawler),
        agent=agent,
        profiles=registry_profiles(engine),
        bus=bus,
        listener=LiveListener(settings.database_url, engine, bus, settings.merchant_tz),
        engine=engine,
        accounts=accounts,
        registry=registry_records(engine),
        bookings=ManagementService(engine, agent, tz=tz),
    )


def __getattr__(name: str) -> Any:
    """`uvicorn merchant_platform.api:app`: build the app on first access, so that importing
    this module doesn't require DATABASE_URL."""
    if name == "app":
        return _default_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
