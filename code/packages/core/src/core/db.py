"""Async SQLAlchemy engine and schema-per-merchant connection helpers.

Merchant tables are declared once, in the placeholder schema `MERCHANT`. `merchant_conn`
maps it to the merchant's real schema (`m_<slug>`) with `schema_translate_map`, which
only rewrites Core constructs: never write a merchant query with `text()`.
"""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, get_args

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import (
    DDL,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Row,
    Sequence,
    Table,
    Text,
    event,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import (
    ARRAY,
    JSONB,
    TSTZRANGE,
    UUID,
    ExcludeConstraint,
    Range,
)
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from core.availability import BusyInterval
from core.config import get_settings
from core.errors import InvalidInput
from core.models import ActivityKind, ReservationStatus, Slug, Via
from core.models.base import SLUG_MAX, SLUG_MIN

MERCHANT = "merchant"

# A result row of any shape, as the services pass them around (SQLAlchemy 2.1 rows are
# variadic generics).
AnyRow = Row[*tuple[Any, ...]]

_SLUG = TypeAdapter(Slug)
# `schema_for` in reverse: `m_` + slug with `-` replaced by `_` (slugs have no `_`).
_MERCHANT_SCHEMA = re.compile(rf"^m_([a-z0-9_]{{{SLUG_MIN},{SLUG_MAX}}})$")


def make_engine(url: str | None = None, **pool: int) -> AsyncEngine:
    """Async engine on `url`, or DATABASE_URL (driver `postgresql+psycopg`). One per process,
    plus a small one for a side job that must never take the main pool's connections (`pool`:
    `pool_size`, `max_overflow`)."""
    # hide_parameters: a DB error never carries guest names into logs or messages.
    return create_async_engine(
        url or get_settings().database_url, pool_pre_ping=True, hide_parameters=True, **pool
    )


def schema_for(slug: str) -> str:
    """Schema of a merchant: `m_` + slug, `-` → `_`. Injective, since slugs have no `_`."""
    try:
        _SLUG.validate_python(slug, strict=True)
    except ValidationError:
        raise InvalidInput(f"Invalid merchant slug: {repr(slug)[:80]}") from None
    return "m_" + slug.replace("-", "_")


def slug_for_schema(schema: str) -> str | None:
    """The slug whose schema is `schema` (`schema_for` in reverse); None for another schema."""
    match = _MERCHANT_SCHEMA.match(schema)
    return match.group(1).replace("_", "-") if match else None


@asynccontextmanager
async def merchant_conn(engine: AsyncEngine, slug: str) -> AsyncIterator[AsyncConnection]:
    """One transaction on the merchant's schema: commits on exit, rolls back on error."""
    translate = {MERCHANT: schema_for(slug)}
    async with engine.begin() as conn:
        yield await conn.execution_options(schema_translate_map=translate)


# Who writes in a transaction, for the live events (#134): `notify_reservation()` passes it on
# (migration 0005), and the listener sends the merchant only its guests' changes.
ACTOR_SETTING = "sesame.actor"


async def as_merchant(conn: AsyncConnection) -> None:
    """Mark the transaction's reservation writes as the merchant's own (transaction-local)."""
    await conn.execute(select(func.set_config(ACTOR_SETTING, "merchant", True)))


def _one_of(name: str, values: tuple[str, ...]) -> CheckConstraint:
    """`name IN (...)` from the contract's Literal values (code constants, never input)."""
    allowed = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{name} IN ({allowed})")


# public: managed by Alembic (migrations 0001-0008), declared here to query it.

public_metadata = MetaData(schema="public")

merchants_t = Table(
    "merchants",
    public_metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()),
    Column("slug", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("website", Text, nullable=False),
    Column("fallback_url", Text, nullable=False),
    Column("city", Text),
    Column("tags", ARRAY(Text), nullable=False, server_default=text("'{}'")),
    Column("price_level", Text, nullable=False),
    Column("status", Text, nullable=False, server_default=text("'building'")),
    Column("profile", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

guest_notices_t = Table(
    "guest_notices",
    public_metadata,
    Column("id", BigInteger, primary_key=True),
    Column("guest_name", Text, nullable=False),
    Column("merchant_slug", Text, ForeignKey(merchants_t.c.slug), nullable=False),
    Column("reservation_id", Text, nullable=False),
    Column("message", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("delivered_at", DateTime(timezone=True)),
)

# One row per update a guest applied, by its idempotency key (#78). In `public`, managed by
# Alembic like the notices, so existing merchant schemas need no re-provision (ADR-0013).
reservation_changes_t = Table(
    "reservation_changes",
    public_metadata,
    Column(
        "merchant_slug", Text, ForeignKey(merchants_t.c.slug, ondelete="CASCADE"), primary_key=True
    ),
    Column("idempotency_key", Text, primary_key=True),
    Column("reservation_id", Text, nullable=False),
    Column("starts_at", DateTime(timezone=True)),
    Column("party_size", Integer),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# What other agents asked each merchant's agent over BATP, with both frames, kept 7 days
# (#146, migration 0003). Holds guest names: only the merchant's own session reads it.
agent_requests_t = Table(
    "agent_requests",
    public_metadata,
    Column("id", BigInteger, primary_key=True),
    Column(
        "merchant_slug", Text, ForeignKey(merchants_t.c.slug, ondelete="CASCADE"), nullable=False
    ),
    Column("at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("agent_name", Text, nullable=False),
    Column("intent", Text, nullable=False),
    Column("status", Integer, nullable=False),
    # The frames as the text that was on the wire, never re-serialized (migration 0007).
    Column("request", Text, nullable=False),
    Column("response", Text, nullable=False),
)

merchant_owners_t = Table(
    "merchant_owners",
    public_metadata,
    Column(
        "merchant_slug", Text, ForeignKey(merchants_t.c.slug, ondelete="CASCADE"), primary_key=True
    ),
    Column("owner_name", Text),
    # SHA-256 of the secret link's token (#132, migration 0004): the token is never stored.
    Column("token_hash", Text, nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# The public site's screenshots, stored with the build for Studio (#151, migration 0008).
merchant_sites_t = Table(
    "merchant_sites",
    public_metadata,
    Column(
        "merchant_slug", Text, ForeignKey(merchants_t.c.slug, ondelete="CASCADE"), primary_key=True
    ),
    Column("pages", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# What changed at each merchant, worded at read time for Home's feed (#135, migration 0006).
# Holds guest names: only the merchant's owner reads it.
merchant_activity_t = Table(
    "merchant_activity",
    public_metadata,
    Column("id", BigInteger, primary_key=True),
    Column(
        "merchant_slug", Text, ForeignKey(merchants_t.c.slug, ondelete="CASCADE"), nullable=False
    ),
    Column("at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("kind", Text, _one_of("kind", get_args(ActivityKind)), nullable=False),
    Column("source", Text, _one_of("source", get_args(Via)), nullable=False),
    Column("reservation_id", Text),
    Column("details", JSONB, nullable=False, server_default=text("'{}'")),
    Index("merchant_activity_latest", "merchant_slug", text("at DESC")),
)

# Merchant schema: created by provisioning (`merchant_metadata.create_all` on a translated
# connection), one copy per merchant.

merchant_metadata = MetaData(schema=MERCHANT)

tables_t = Table(
    "tables",
    merchant_metadata,
    Column("id", Integer, primary_key=True),
    Column("label", Text, nullable=False, unique=True),
    Column("seats", Integer, CheckConstraint("seats > 0"), nullable=False),
)

policy_t = Table(
    "policy",
    merchant_metadata,
    Column(
        "id",
        Integer,
        CheckConstraint("id = 1"),
        primary_key=True,
        autoincrement=False,
        server_default=text("1"),
    ),
    Column("max_party_size", Integer, nullable=False),
    Column("slot_length_min", Integer, nullable=False, server_default=text("120")),
    Column("services", JSONB, nullable=False),
)

blocked_slots_t = Table(
    "blocked_slots",
    merchant_metadata,
    Column("starts_at", DateTime(timezone=True), primary_key=True),
)

# Reservation IDs come from Python (`next_reservation_id`): a server default naming the
# sequence in SQL text would escape the schema translation.
reservation_seq = Sequence("reservation_seq", metadata=merchant_metadata, schema=MERCHANT)

reservations_t = Table(
    "reservations",
    merchant_metadata,
    Column("id", Text, primary_key=True),
    Column("table_id", Integer, ForeignKey(tables_t.c.id), nullable=False),
    Column("guest_name", Text, nullable=False),
    Column("party_size", Integer, CheckConstraint("party_size > 0"), nullable=False),
    # Not empty: an empty range overlaps nothing, so it would slip past the exclusion below.
    Column("during", TSTZRANGE, CheckConstraint("NOT isempty(during)"), nullable=False),
    Column("via", Text, _one_of("via", get_args(Via)), nullable=False),
    Column(
        "status",
        Text,
        _one_of("status", get_args(ReservationStatus)),
        nullable=False,
        server_default=text("'confirmed'"),
    ),
    Column("cancelled_by", Text, _one_of("cancelled_by", ("guest", "merchant"))),
    Column("idempotency_key", Text, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    # No two confirmed bookings may overlap on the same table: double booking is impossible
    # at the database level, whatever the application does.
    ExcludeConstraint(
        ("table_id", "="),
        ("during", "&&"),
        name="reservations_no_overlap",
        using="gist",
        where="status = 'confirmed'",
    ),
)

# A booking's start, the lower bound of `during`, as every query selects, filters and sorts it.
reservation_start = func.lower(reservations_t.c.during)
# A booking that holds its table: the bookings the exclusion constraint keeps apart.
reservation_confirmed = reservations_t.c.status == "confirmed"


@dataclass(frozen=True)
class BookedInterval(BusyInterval):
    """A confirmed booking's hold on its table, with its reference and party: what Home's room
    and a merchant's move need besides the interval."""

    reservation_id: str
    party_size: int


async def busy_between(
    conn: AsyncConnection, start: datetime, end: datetime
) -> list[BookedInterval]:
    """The confirmed bookings whose range overlaps [start, end), for the availability rules."""
    rows = await conn.execute(
        select(
            reservations_t.c.id,
            reservations_t.c.table_id,
            reservations_t.c.during,
            reservations_t.c.party_size,
        ).where(reservation_confirmed, reservations_t.c.during.overlaps(Range(start, end)))
    )
    return [
        BookedInterval(row.table_id, row.during.lower, row.during.upper, row.id, row.party_size)
        for row in rows
    ]


async def blocked_between(conn: AsyncConnection, start: datetime, end: datetime) -> list[datetime]:
    """The blocked start times in [start, end), by time."""
    starts_at = blocked_slots_t.c.starts_at
    return list(
        await conn.scalars(
            select(starts_at).where(starts_at >= start, starts_at < end).order_by(starts_at)
        )
    )


# Live events: every write on a merchant's reservations notifies the `reservations` channel
# (function from migration 0001). `%(fullname)s` goes through the schema translation.
event.listen(
    reservations_t,
    "after_create",
    DDL(  # type: ignore[no-untyped-call]
        "CREATE TRIGGER reservations_notify AFTER INSERT OR UPDATE ON %(fullname)s "
        "FOR EACH ROW EXECUTE FUNCTION public.notify_reservation()"
    ),
)
# An UPDATE that changes nothing (an idempotent replay) is skipped before it happens, so it
# never shows the merchant another "Booking updated" card.
event.listen(
    reservations_t,
    "after_create",
    DDL(  # type: ignore[no-untyped-call]
        "CREATE TRIGGER reservations_skip_noop BEFORE UPDATE ON %(fullname)s "
        "FOR EACH ROW EXECUTE FUNCTION suppress_redundant_updates_trigger()"
    ),
)


async def next_reservation_id(conn: AsyncConnection) -> str:
    """Next human reference (`R-001`, …) from the merchant's own sequence.

    Sequences ignore rollbacks: a booking rejected after taking an ID leaves a gap.
    """
    number = await conn.scalar(select(reservation_seq.next_value()))
    return f"R-{number:03d}"
