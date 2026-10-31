"""Async business-service contract shared by the gateway and platform."""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Literal, Protocol, overload

from core.models import (
    AdminReservationRow,
    Policy,
    Reservation,
    Service,
    Slot,
    Stats,
    TableSpec,
    Via,
)


class AgentService(Protocol):
    """Implementations raise core.errors.AgentError for business failures."""

    async def get_availability(
        self, slug: str, day: date, party_size: int, time: str | None = None
    ) -> list[Slot]: ...

    async def create_reservation(
        self,
        slug: str,
        starts_at: datetime,
        party_size: int,
        guest_name: str,
        idempotency_key: str,
        via: Via,
    ) -> Reservation: ...

    async def update_reservation(
        self,
        slug: str,
        reservation_id: str,
        guest_name: str,
        idempotency_key: str,
        starts_at: datetime | None = None,
        party_size: int | None = None,
    ) -> Reservation: ...

    @overload
    async def cancel_reservation(
        self, slug: str, reservation_id: str, *, by: Literal["guest"], guest_name: str
    ) -> Reservation: ...

    @overload
    async def cancel_reservation(
        self, slug: str, reservation_id: str, *, by: Literal["merchant"], guest_name: None = None
    ) -> Reservation: ...

    async def find_guest_reservations(
        self, guest_name: str
    ) -> tuple[list[Reservation], list[str]]: ...

    async def list_reservations(self, slug: str, day: date) -> list[AdminReservationRow]: ...

    async def get_stats(self, slug: str, day: date) -> Stats: ...

    async def block_slot(self, slug: str, starts_at: datetime) -> None: ...

    async def unblock_slot(self, slug: str, starts_at: datetime) -> None: ...

    async def update_policy(
        self,
        slug: str,
        max_party_size: int | None = None,
        services: Sequence[Service] | None = None,
    ) -> Policy: ...

    async def update_tables(self, slug: str, tables: Sequence[TableSpec]) -> list[TableSpec]: ...
