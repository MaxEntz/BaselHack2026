"""What one operation needs of a merchant, read once on its connection: its name, policy and
floor plan (#9). Guests' operations, the merchant's and the management site's all load it here.
"""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from core.availability import TableRow
from core.db import merchants_t, policy_t, tables_t
from core.errors import NotFound, PartyTooLarge
from core.models import Service


@dataclass(frozen=True)
class Merchant:
    name: str
    max_party_size: int
    slot_length: timedelta
    services: tuple[Service, ...]  # by start
    tables: tuple[TableRow, ...]

    @property
    def largest_group(self) -> int:
        """The largest party it takes online: its limit, or its largest table when smaller."""
        return min(self.max_party_size, max((t.seats for t in self.tables), default=0))

    def check_party(self, party_size: int) -> None:
        """Too large for the policy or for every table: the guest gets the fallback link."""
        if party_size > self.largest_group:
            raise PartyTooLarge(
                f"{self.name} takes online bookings for up to {self.largest_group} guests"
            )


async def load_merchant(
    conn: AsyncConnection, slug: str, *, merchant_side: bool = False
) -> Merchant:
    """The merchant's name, policy and tables. Guests can't reach a merchant still
    `building`; the merchant can (`merchant_side`), to fix its plan during onboarding."""
    registry = (
        await conn.execute(
            select(merchants_t.c.name, merchants_t.c.status).where(merchants_t.c.slug == slug)
        )
    ).one_or_none()
    if registry is None:
        raise NotFound(f"Unknown merchant: {slug!r}")
    if registry.status == "building" and not merchant_side:
        raise NotFound(f"Merchant {slug!r} is not live yet")
    policy = (await conn.execute(select(policy_t))).one()
    tables = await conn.execute(select(tables_t.c.id, tables_t.c.label, tables_t.c.seats))
    return Merchant(
        name=registry.name,
        max_party_size=policy.max_party_size,
        slot_length=timedelta(minutes=policy.slot_length_min),
        # Older policies may store them in another order; the order matters to Home's room
        # and to the Reservations page, never to the availability rules.
        services=tuple(
            sorted((Service.model_validate(s) for s in policy.services), key=lambda s: s.start)
        ),
        tables=tuple(TableRow(id=row.id, label=row.label, seats=row.seats) for row in tables),
    )
