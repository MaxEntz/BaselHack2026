"""The merchant agents behind the BATP node: one handler per intent (PROTOCOL.md §7).

`TALK` goes to the merchant's AI agent (`agent.talk`, #246, #249), within the node's limits
(`limits.py`); without OPENAI_API_KEY there is no agent, and it answers `503 agent_unavailable`
with the fallback URL while the other intents work as before.

Params and results are the tool contracts of `core.models`, so BATP and the MCP tools agree by
construction. A merchant marked `down` refuses agent traffic with its fallback URL, as the
gateway does for Claude (#13); its own website keeps booking through the platform.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.discovery import RegistryEntry, find_merchant, routable, search
from agent.service import AgentService
from agent.talk import MerchantAgent
from batp.messages import AgentIdentity, Intent, Request
from batp.server import FAILED
from batp.status import WITH_FALLBACK, Refused, TooManyRequests
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
    TalkInput,
    UpdateReservationInput,
    Via,
)
from merchant_node.limits import TalkLimits

# Bookings over BATP come from guests' agents. The `kind` a caller declares is not trusted, and
# `Via` has no value for agents other than Claude yet: every BATP booking is `claude`.
VIA: Via = "claude"
NO_AGENT = "{name}'s agent can't talk right now; book on its website"

log = logging.getLogger("merchant_node")

Result = dict[str, Any]
IntentHandler = Callable[[AgentIdentity, Request], Awaitable[Result]]


class MerchantAgents:
    """`NodeServer`'s handler: validates params, calls the service, maps refusals."""

    def __init__(
        self,
        engine: AsyncEngine,
        service: AgentService,
        talker: MerchantAgent | None = None,
        limits: TalkLimits | None = None,
    ) -> None:
        """`talker` answers `TALK`: none without OPENAI_API_KEY."""
        self.engine = engine
        self.service = service
        self.talker = talker
        self.limits = limits if limits is not None else TalkLimits()
        self._handlers: dict[Intent, IntentHandler] = {
            "PING": self._ping,
            "DISCOVER": self._discover,
            "AVAILABILITY": self._availability,
            "BOOK": self._book,
            "AMEND": self._amend,
            "CANCEL": self._cancel,
            "TALK": self._talk,
            "RECALL": self._recall,
        }

    async def __call__(self, agent: AgentIdentity, request: Request) -> Result:
        try:
            return await self._handlers[request.intent](agent, request)
        except ValidationError as error:
            fields = sorted({".".join(map(str, e["loc"]))[:60] for e in error.errors()})
            listed = ", ".join(fields[:5]) or "params"
            raise InvalidInput(f"Invalid params for {request.intent}: {listed}") from None
        except (Refused, AgentError, TooManyRequests):
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
        return _dump(GetAvailabilityOutput(slots=tuple(slots)))

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

    async def _talk(self, agent: AgentIdentity, request: Request) -> Result:
        slug, params = await self._merchant(request)
        inp = TalkInput.model_validate(params)
        if self.talker is None:
            merchant = await self._registry(slug)
            raise Refused(
                AgentUnavailable(NO_AGENT.format(name=merchant.name)), merchant.fallback_url
            )
        async with self.limits.turn(slug):
            output = await self._refusing(slug, self.talker.talk(slug, agent.id, inp))
        return _dump(output)

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
        merchant = await self._registry(request.merchant)
        if merchant.paused:
            raise Refused(
                AgentUnavailable(
                    f"{merchant.name} is not taking bookings through agents right now"
                ),
                merchant.fallback_url,
            )
        # The request's merchant wins over any `merchant` inside the params.
        return request.merchant, {**request.params, "merchant": request.merchant}

    async def _registry(self, slug: str) -> RegistryEntry:
        """The merchant's registry entry, if agents may address it (`discovery.routable`)."""
        merchant = routable(await find_merchant(slug, engine=self.engine))
        if merchant is None:
            raise NotFound(f"Unknown merchant: {slug!r}")
        return merchant

    async def fallback_url(self, request: Request) -> str | None:
        """For `NodeServer`: the addressed merchant's booking page, for a 503 it sends itself."""
        return await self._fallback_or_none(request.merchant)

    async def _fallback_or_none(self, slug: str | None) -> str | None:
        if slug is None:
            return None
        try:
            merchant = await self._registry(slug)
        except NotFound:
            # Unknown or not yet published: there is no booking page to send the guest to.
            log.debug("no fallback url: unknown merchant", extra={"merchant": slug})
            return None
        except Exception:
            log.warning("no fallback url", extra={"merchant": slug}, exc_info=True)
            return None
        return merchant.fallback_url

    async def _refusing[T](self, slug: str, call: Awaitable[T]) -> T:
        """Run a service call; a refusal that sends the guest elsewhere gets the fallback URL."""
        try:
            return await call
        except AgentError as error:
            if error.code not in WITH_FALLBACK:
                raise
            raise Refused(error, (await self._registry(slug)).fallback_url) from None


def _dump(model: BaseModel) -> Result:
    return model.model_dump(mode="json")
