"""How the protocol reaches a merchant's agent, whichever process it runs in.

The agent runs in the gateway's process in the POC (AGENTS.md §13). Tools only know this
interface, so an HTTP or gRPC client can replace `LocalAgentClient` without touching them.
"""

from datetime import date, datetime
from typing import Protocol

from agent.service import AgentService
from core.models import Reservation, Slot


class AgentClient(Protocol):
    """The user operations of `AgentService`. Failures raise `core.errors.AgentError`."""

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


class LocalAgentClient:
    """In-process calls. What goes through here comes from Claude: `via` and `by` are fixed."""

    def __init__(self, service: AgentService) -> None:
        self.service = service

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
