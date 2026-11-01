"""The async business-service contract shared by the gateway, the BATP node and the platform.

Frozen (#2): it changes only in a dedicated PR announced to the team. `agent.service` re-exports
it with its Postgres implementation, `PgAgentService`; callers import it from there.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Protocol, overload

from core.models import (
    AdminReservationRow,
    AgentRequest,
    BlockedTime,
    GuestRow,
    KnowledgeChange,
    KnowledgeNote,
    MerchantCancelOutcome,
    MerchantSettings,
    Policy,
    PriceLevel,
    Reservation,
    Rules,
    Service,
    Slot,
    Stats,
    TableSpec,
    Tag,
    Via,
)

# What the merchant can switch its published agent to: paused (`down`) or `live` (#146).
AgentStatus = Literal["live", "down"]


@dataclass(frozen=True)
class MerchantCancellation:
    """The booking after a merchant's cancellation, and what was done: the merchant must never
    be told the guest will be notified when no notice was stored (#82)."""

    reservation: Reservation
    outcome: MerchantCancelOutcome


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

    async def cancel_by_merchant(self, slug: str, reservation_id: str) -> MerchantCancellation: ...

    async def find_guest_reservations(
        self, guest_name: str
    ) -> tuple[list[Reservation], list[str]]: ...

    async def list_reservations(self, slug: str, day: date) -> list[AdminReservationRow]: ...

    async def get_stats(self, slug: str, day: date) -> Stats: ...

    async def list_guests(self, slug: str, query: str = "") -> list[GuestRow]: ...

    async def block_slot(self, slug: str, starts_at: datetime) -> None: ...

    async def unblock_slot(self, slug: str, starts_at: datetime) -> None: ...

    async def update_policy(
        self,
        slug: str,
        max_party_size: int | None = None,
        services: Sequence[Service] | None = None,
    ) -> Policy: ...

    async def update_tables(self, slug: str, tables: Sequence[TableSpec]) -> list[TableSpec]: ...

    async def update_knowledge(
        self, slug: str, remove: Sequence[str], add: Sequence[KnowledgeNote]
    ) -> KnowledgeChange:
        """Refused with `InvalidInput` when the result wouldn't fit `SiteKnowledge`'s bounds."""
        ...

    async def set_status(self, slug: str, status: AgentStatus) -> None:
        """Pause (`down`) or resume (`live`) the merchant's agent (#146)."""
        ...

    async def list_agent_requests(
        self, slug: str, day: date, *, refused: bool = False
    ) -> list[AgentRequest]:
        """What other agents asked the merchant's agent on `day`, newest first, 10 at most
        (#146). Rows hold guest names: the merchant's side only."""
        ...

    # The management site's forms (#133): each save keeps the registry in step.

    async def get_policy(self, slug: str) -> Policy:
        """Services by start, the group limit and the slot length."""
        ...

    async def get_tables(self, slug: str) -> list[TableSpec]:
        """The floor plan, in the order it was last saved."""
        ...

    async def get_rules(self, slug: str) -> Rules: ...

    async def update_rules(
        self,
        slug: str,
        max_party_size: int,
        fallback_url: str,
        cancellation_policy: str | None,
    ) -> Rules:
        """Refused with `booking_page_off_site` when the page leaves the merchant's website."""
        ...

    async def get_settings(self, slug: str) -> MerchantSettings: ...

    async def update_settings(
        self,
        slug: str,
        name: str,
        city: str | None,
        price_level: PriceLevel,
        tags: Sequence[Tag],
    ) -> MerchantSettings: ...

    async def list_blocked(self, slug: str) -> list[BlockedTime]:
        """The blocked starts to come."""
        ...

    async def set_blocked(self, slug: str, day: date, times: Sequence[str]) -> list[BlockedTime]:
        """The day's blocked starts become `times` (local `HH:MM`); answers those to come."""
        ...
