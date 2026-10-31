"""The merchant agents behind the BATP node: one handler per intent (PROTOCOL.md §7).

Params and results are the tool contracts of `core.models`, so BATP and the MCP tools agree by
construction. A merchant marked `down` refuses agent traffic with its fallback URL, as the
gateway does for Claude (#13); its own website keeps booking through the platform.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.service import AgentService
from batp.messages import AgentIdentity, Intent, Request
from batp.server import FAILED
from batp.status import WITH_FALLBACK, Refused
from core.db import merchants_t
from core.errors import AgentError, AgentUnavailable, InvalidInput, NotFound
from core.models import (
    CancelReservationInput,
    CreateReservationInput,
    GetAvailabilityInput,
    GetAvailabilityOutput,
    GetMyReservationsInput,
    GetMyReservationsOutput,
    ReservationOutput,
    SearchMerchantsInput,
    UpdateReservationInput,
    Via,
)
from gateway.discovery import search

# Bookings over BATP come from guests' agents. The `kind` a caller declares is not trusted, and
# `Via` has no value for agents other than Claude yet: every BATP booking is `claude`.
VIA: Via = "claude"

log = logging.getLogger("merchant_node")

Result = dict[str, Any]
IntentHandler = Callable[[AgentIdentity, Request], Awaitable[Result]]


class MerchantAgents:
    """`NodeServer`'s handler: validates params, calls the service, maps refusals."""

    def __init__(self, engine: AsyncEngine, service: AgentService) -> None:
        self.engine = engine
        self.service = service
        self._handlers: dict[Intent, IntentHandler] = {
            "PING": self._ping,
            "DISCOVER": self._discover,
            "AVAILABILITY": self._availability,
            "BOOK": self._book,
            "AMEND": self._amend,
            "CANCEL": self._cancel,
            "RECALL": self._recall,
        }

    async def __call__(self, agent: AgentIdentity, request: Request) -> Result:
        try:
            return await self._handlers[request.intent](agent, request)
        except ValidationError as error:
            fields = sorted({".".join(map(str, e["loc"]))[:60] for e in error.errors()})
            listed = ", ".join(fields[:5]) or "params"
            raise InvalidInput(f"Invalid params for {request.intent}: {listed}") from None
        except (Refused, AgentError):
            raise
        except Exception:
            # Never a trace for the caller, but still the merchant's fallback URL (§8).
            log.exception("intent failed", extra={"agent": agent.id, "intent": request.intent})
            fallback_url = await self._fallback_or_none(request.merchant)
            raise Refused(AgentUnavailable(FAILED), fallback_url) from None

    async def _ping(self, agent: AgentIdentity, request: Request) -> Result:
        return {"pong": True, "server_time": datetime.now(UTC).isoformat()}

    async def _discover(self, agent: AgentIdentity, request: Request) -> Result:
        inp = SearchMerchantsInput.model_validate(request.params)
        # `search` refuses a query or filters beyond its bounds before reading the registry.
        found = await search(inp.query, inp.near, inp.date, inp.filters, engine=self.engine)
        return _dump(found)

    async def _availability(self, agent: AgentIdentity, request: Request) -> Result:
        slug, params = await self._merchant(request)
        inp = GetAvailabilityInput.model_validate(params)
        slots = await self._refusing(
            slug, self.service.get_availability(slug, inp.date, inp.party_size, inp.time)
        )
        return _dump(GetAvailabilityOutput(slots=tuple(slots[:3])))

    async def _book(self, agent: AgentIdentity, request: Request) -> Result:
        slug, params = await self._merchant(request)
        inp = CreateReservationInput.model_validate(params)
        reservation = await self._refusing(
            slug,
            self.service.create_reservation(
                slug,
                inp.starts_at,
                inp.party_size,
                inp.guest_name,
                inp.idempotency_key,
                via=VIA,
            ),
        )
        return _dump(ReservationOutput(reservation=reservation))

    async def _amend(self, agent: AgentIdentity, request: Request) -> Result:
        slug, params = await self._merchant(request)
        inp = UpdateReservationInput.model_validate(params)
        reservation = await self._refusing(
            slug,
            self.service.update_reservation(
                slug,
                inp.reservation_id,
                inp.guest_name,
                inp.idempotency_key,
                starts_at=inp.starts_at,
                party_size=inp.party_size,
            ),
        )
        return _dump(ReservationOutput(reservation=reservation))

    async def _cancel(self, agent: AgentIdentity, request: Request) -> Result:
        slug, params = await self._merchant(request)
        inp = CancelReservationInput.model_validate(params)
        reservation = await self._refusing(
            slug,
            self.service.cancel_reservation(
                slug, inp.reservation_id, by="guest", guest_name=inp.guest_name
            ),
        )
        return _dump(ReservationOutput(reservation=reservation))

    async def _recall(self, agent: AgentIdentity, request: Request) -> Result:
        inp = GetMyReservationsInput.model_validate(request.params)
        reservations, notices = await self.service.find_guest_reservations(inp.guest_name)
        return _dump(
            GetMyReservationsOutput(reservations=tuple(reservations), notices=tuple(notices))
        )

    async def _merchant(self, request: Request) -> tuple[str, dict[str, Any]]:
        """The addressed merchant, if it takes agent traffic, and its params for the contract."""
        if request.merchant is None:
            raise InvalidInput(f"{request.intent} needs a merchant")
        status, fallback_url, name = await self._registry(request.merchant)
        if status == "down":
            raise Refused(
                AgentUnavailable(f"{name} is not taking bookings through agents right now"),
                fallback_url,
            )
        # The request's merchant wins over any `merchant` inside the params.
        return request.merchant, {**request.params, "merchant": request.merchant}

    async def _registry(self, slug: str) -> tuple[str, str, str]:
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    select(
                        merchants_t.c.status, merchants_t.c.fallback_url, merchants_t.c.name
                    ).where(merchants_t.c.slug == slug)
                )
            ).one_or_none()
        if row is None or row.status == "building":
            raise NotFound(f"Unknown merchant: {slug!r}")
        return row.status, row.fallback_url, row.name

    async def fallback_url(self, request: Request) -> str | None:
        """For `NodeServer`: the addressed merchant's booking page, for a 503 it sends itself."""
        return await self._fallback_or_none(request.merchant)

    async def _fallback_or_none(self, slug: str | None) -> str | None:
        if slug is None:
            return None
        try:
            _, fallback_url, _ = await self._registry(slug)
        except Exception:
            return None
        return fallback_url

    async def _refusing[T](self, slug: str, call: Awaitable[T]) -> T:
        """Run a service call; a refusal that sends the guest elsewhere gets the fallback URL."""
        try:
            return await call
        except AgentError as error:
            if error.code not in WITH_FALLBACK:
                raise
            _, fallback_url, _ = await self._registry(slug)
            raise Refused(error, fallback_url) from None


def _dump(model: BaseModel) -> Result:
    return model.model_dump(mode="json")
