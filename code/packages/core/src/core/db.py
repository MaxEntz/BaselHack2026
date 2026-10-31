"""Async SQLAlchemy engine and schema-per-merchant connection helpers.

Merchant tables are declared once, in the placeholder schema `MERCHANT`. `merchant_conn`
maps it to the merchant's real schema (`m_<slug>`) with `schema_translate_map`, which
only rewrites Core constructs: never write a merchant query with `text()`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import get_args

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import (
    DDL,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Sequence,
    Table,
    Text,
    event,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSTZRANGE, UUID, ExcludeConstraint
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from core.config import get_settings
from core.errors import InvalidInput
from core.models import ReservationStatus, Slug, Via

MERCHANT = "merchant"

_SLUG = TypeAdapter(Slug)


def make_engine(url: str | None = None) -> AsyncEngine:
    """Async engine on `url`, or DATABASE_URL (driver `postgresql+psycopg`). One per process."""
    # hide_parameters: a DB error never carries guest names into logs or messages.
    return create_async_engine(
        url or get_settings().database_url, pool_pre_ping=True, hide_parameters=True
    )


def schema_for(slug: str) -> str:
    """Schema of a merchant: `m_` + slug, `-` → `_`. Injective, since slugs have no `_`."""
    try:
        _SLUG.validate_python(slug, strict=True)
    except ValidationError:
        raise InvalidInput(f"Invalid merchant slug: {repr(slug)[:80]}") from None
    return "m_" + slug.replace("-", "_")


@asynccontextmanager
async def merchant_conn(engine: AsyncEngine, slug: str) -> AsyncIterator[AsyncConnection]:
    """One transaction on the merchant's schema: commits on exit, rolls back on error."""
    translate = {MERCHANT: schema_for(slug)}
    async with engine.begin() as conn:
        yield await conn.execution_options(schema_translate_map=translate)


def _one_of(name: str, values: tuple[str, ...]) -> CheckConstraint:
    """`name IN (...)` from the contract's Literal values (code constants, never input)."""
    allowed = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{name} IN ({allowed})")


# public: managed by Alembic (migration 0001), declared here to query it.

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
