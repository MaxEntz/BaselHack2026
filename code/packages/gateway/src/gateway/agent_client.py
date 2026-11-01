"""How the protocol reaches a merchant's agent, whichever process it runs in.

`LocalAgentClient` calls the agent in the gateway's process. `BatpAgentClient` makes the gateway a
bridge: Claude still speaks MCP to it, and it speaks BATP to the node serving the merchant agents
(`documentation/PROTOCOL.md`, §12). Tools only know the `AgentClient` interface.
"""

from datetime import date, datetime
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from agent import discovery
from agent.service import AgentService, PgAgentService
from batp.client import BatpClient, parse_batp_url
from batp.messages import AgentIdentity, Intent
from core.config import Settings
from core.errors import AgentUnavailable
from core.models import (
    CancelReservationInput,
    CreateReservationInput,
    GetAvailabilityInput,
    GetAvailabilityOutput,
    GetMyReservationsInput,
    GetMyReservationsOutput,
    Reservation,
    ReservationOutput,
    SearchFilters,
    SearchMerchantsInput,
    SearchMerchantsOutput,
    Slot,
    UpdateReservationInput,
)
from gateway.fallback import batp_request

GATEWAY_AGENT = AgentIdentity(
    id="urn:batp:agent:claude-gateway",
    name="Claude via the Sésame gateway",
    kind="user-agent",
    operator="merchant-agents",
)


class AgentClient(Protocol):
    """The user operations of `AgentService`. Failures raise `core.errors.AgentError`."""

    async def search_merchants(
        self, query: str, near: str | None, day: date | None, filters: SearchFilters | None
    ) -> SearchMerchantsOutput: ...

    async def get_availability(
        self, merchant: str, day: date, party_size: int, time: str | None = None
    ) -> list[Slot]: ...

    async def create_reservation(
        self,
        merchant: str,
        starts_at: datetime,
        party_size: int,
        guest_name: str,
        idempotency_key: str,
    ) -> Reservation: ...

    async def update_reservation(
        self,
        merchant: str,
        reservation_id: str,
        guest_name: str,
        idempotency_key: str,
        starts_at: datetime | None = None,
        party_size: int | None = None,
    ) -> Reservation: ...

    async def cancel_reservation(
        self, merchant: str, reservation_id: str, guest_name: str
    ) -> Reservation: ...

    async def get_my_reservations(self, guest_name: str) -> tuple[list[Reservation], list[str]]: ...

    async def close(self) -> None: ...


class LocalAgentClient:
    """In-process calls. What goes through here comes from Claude: `via` and `by` are fixed."""

    def __init__(self, service: AgentService, engine: AsyncEngine) -> None:
        self.service = service
        self.engine = engine

    async def search_merchants(
        self, query: str, near: str | None, day: date | None, filters: SearchFilters | None
    ) -> SearchMerchantsOutput:
        return await discovery.search(query, near, day, filters, engine=self.engine)

    async def get_availability(
        self, merchant: str, day: date, party_size: int, time: str | None = None
    ) -> list[Slot]:
        return await self.service.get_availability(merchant, day, party_size, time)

    async def create_reservation(
        self,
        merchant: str,
        starts_at: datetime,
        party_size: int,
        guest_name: str,
        idempotency_key: str,
    ) -> Reservation:
        return await self.service.create_reservation(
            merchant, starts_at, party_size, guest_name, idempotency_key, via="claude"
        )

    async def update_reservation(
        self,
        merchant: str,
        reservation_id: str,
        guest_name: str,
        idempotency_key: str,
        starts_at: datetime | None = None,
        party_size: int | None = None,
    ) -> Reservation:
        return await self.service.update_reservation(
            merchant, reservation_id, guest_name, idempotency_key, starts_at, party_size
        )

    async def cancel_reservation(
        self, merchant: str, reservation_id: str, guest_name: str
    ) -> Reservation:
        return await self.service.cancel_reservation(
            merchant, reservation_id, by="guest", guest_name=guest_name
        )

    async def get_my_reservations(self, guest_name: str) -> tuple[list[Reservation], list[str]]:
        return await self.service.find_guest_reservations(guest_name)

    async def close(self) -> None:
        pass


class BatpAgentClient:
    """The same operations over BATP. `params` and `result` are the contracts of `core.models`."""

    def __init__(self, client: BatpClient) -> None:
        self.client = client

    async def _request[M: BaseModel](
        self, intent: Intent, merchant: str | None, params: BaseModel, result: type[M]
    ) -> M:
        """The node's answer. Its errors and a lost session are `AgentError`s already."""
        request_id = f"r-{uuid4().hex[:12]}"
        batp_request.set(request_id)
        wire = params.model_dump(mode="json", exclude={"merchant"}, exclude_none=True)
        response = await self.client.request(intent, merchant, wire, request_id=request_id)
        try:
            return result.model_validate(response)
        except ValidationError as exc:
            raise AgentUnavailable(f"The BATP node sent an invalid {intent} result") from exc

    async def search_merchants(
        self, query: str, near: str | None, day: date | None, filters: SearchFilters | None
    ) -> SearchMerchantsOutput:
        params = SearchMerchantsInput(query=query, near=near, date=day, filters=filters)
        return await self._request("DISCOVER", None, params, SearchMerchantsOutput)

    async def get_availability(
        self, merchant: str, day: date, party_size: int, time: str | None = None
    ) -> list[Slot]:
        params = GetAvailabilityInput(merchant=merchant, date=day, time=time, party_size=party_size)
        result = await self._request("AVAILABILITY", merchant, params, GetAvailabilityOutput)
        return list(result.slots)

    async def create_reservation(
        self,
        merchant: str,
        starts_at: datetime,
        party_size: int,
        guest_name: str,
        idempotency_key: str,
    ) -> Reservation:
        params = CreateReservationInput(
            merchant=merchant,
            starts_at=starts_at,
            party_size=party_size,
            guest_name=guest_name,
            idempotency_key=idempotency_key,
        )
        result = await self._request("BOOK", merchant, params, ReservationOutput)
        return result.reservation

    async def update_reservation(
        self,
        merchant: str,
        reservation_id: str,
        guest_name: str,
        idempotency_key: str,
        starts_at: datetime | None = None,
        party_size: int | None = None,
    ) -> Reservation:
        params = UpdateReservationInput(
            merchant=merchant,
            reservation_id=reservation_id,
            guest_name=guest_name,
            idempotency_key=idempotency_key,
            starts_at=starts_at,
            party_size=party_size,
        )
        result = await self._request("AMEND", merchant, params, ReservationOutput)
        return result.reservation

    async def cancel_reservation(
        self, merchant: str, reservation_id: str, guest_name: str
    ) -> Reservation:
        params = CancelReservationInput(
            merchant=merchant, reservation_id=reservation_id, guest_name=guest_name
        )
        result = await self._request("CANCEL", merchant, params, ReservationOutput)
        return result.reservation

    async def get_my_reservations(self, guest_name: str) -> tuple[list[Reservation], list[str]]:
        params = GetMyReservationsInput(guest_name=guest_name)
        result = await self._request("RECALL", None, params, GetMyReservationsOutput)
        return list(result.reservations), list(result.notices)

    async def close(self) -> None:
        await self.client.close()


def make_agent_client(settings: Settings, engine: AsyncEngine) -> AgentClient:
    """Over BATP when `BATP_URL` is set, in this process otherwise."""
    if not settings.batp_url:
        service = PgAgentService(engine, tz=ZoneInfo(settings.merchant_tz))
        return LocalAgentClient(service, engine)
    host, port = parse_batp_url(settings.batp_url)
    token = settings.batp_token.get_secret_value() if settings.batp_token else None
    return BatpAgentClient(BatpClient(host, port, GATEWAY_AGENT, token))
