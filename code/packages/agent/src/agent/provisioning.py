"""MerchantProfile -> merchant schema, tables, policy and registry row.

Deterministic code driven by the validated profile and `templates/restaurant.json`: the LLM
only fills the profile, it never produces SQL, code or tool definitions (AGENTS.md §7.1).
"""

from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from string import Formatter
from typing import Any, Literal, Self, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, PositiveInt, ValidationError, model_validator
from sqlalchemy import delete, insert, inspect, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.schema import CreateSchema, DropSchema

from core.db import merchant_conn, merchant_metadata, merchants_t, policy_t, schema_for, tables_t
from core.errors import InvalidInput, NotFound
from core.models import GeneratedTool, MerchantProfile, PriceLevel, Services, TableSpec

RegistryStatus = Literal["building", "live", "down"]
SwitchableStatus = Literal["live", "down"]

# Values a tool description may use, with sample values to render it once at import.
PLACEHOLDER_SAMPLES: dict[str, Any] = {
    "name": "Le Petit Zinc",
    "city": "Mulhouse",
    "services": "19:00-22:00",
    "max_party_size": 8,
    "cancellation_policy": "none stated",
}

# Postgres errors raised by CREATE SCHEMA when the name is taken: duplicate_schema, or a
# unique violation on pg_namespace when a concurrent transaction creates it first.
SCHEMA_TAKEN = frozenset({"42P06", "23505"})

# `seats` may come from the LLM: refuse a plan nobody would believe in a demo.
MAX_DEFAULT_SEATS = 400

# From this size, the default floor plan includes one 6-seat table.
LARGE_ROOM_SEATS = 30


class SlugTaken(InvalidInput):
    """The slug already has a merchant (or a leftover schema): the builder picks another."""


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TemplateDefaults(_Frozen):
    slot_length_min: PositiveInt
    max_party_size: PositiveInt
    services: Services
    price_level: PriceLevel


class ToolTemplate(_Frozen):
    name: str
    description: str

    @model_validator(mode="after")
    def validate_placeholders(self) -> Self:
        used = {
            field for _, field, _, _ in Formatter().parse(self.description) if field is not None
        }
        if unknown := used - PLACEHOLDER_SAMPLES.keys():
            raise ValueError(
                f"Unknown placeholders in {self.name}: {sorted(unknown)}; "
                f"allowed: {sorted(PLACEHOLDER_SAMPLES)}"
            )
        # Catches what the field names don't show: nested fields, bad conversions or specs.
        try:
            self.description.format_map(PLACEHOLDER_SAMPLES)
        except (KeyError, IndexError, ValueError) as error:
            raise ValueError(f"Description of {self.name} does not render: {error!r}") from None
        return self


class RestaurantTemplate(_Frozen):
    vertical: Literal["restaurant"]
    defaults: TemplateDefaults
    default_tables_rule: str
    tools: tuple[ToolTemplate, ...]


TEMPLATE_PATH = files("agent") / "templates" / "restaurant.json"


def load_template(path: Traversable = TEMPLATE_PATH) -> RestaurantTemplate:
    """Read and validate a vertical template; any problem stops the process, naming the file."""
    try:
        return RestaurantTemplate.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as error:
        raise RuntimeError(f"Invalid template {path}: {error}") from error


# Loaded at import: a broken template crashes the process at startup, not mid-onboarding.
TEMPLATE = load_template()


@dataclass(frozen=True)
class MerchantRecord:
    """Registry entry created by `provision`."""

    id: UUID
    slug: str
    schema: str
    status: RegistryStatus


def default_tables(seats: int) -> list[TableSpec]:
    """Floor plan when neither the site nor the description gives one (template rule).

    When `seats >= 30`, 6 of the seats go to one 6-seat table. The rest goes to 4-seat
    tables, then 2-seat tables for the remainder, rounded up so the plan seats at least
    `seats` (at most one extra seat). Labels T1, T2, … with the 6-seat table last. The same
    `seats` always gives the same plan.
    """
    if not 1 <= seats <= MAX_DEFAULT_SEATS:
        raise ValueError(f"seats must be between 1 and {MAX_DEFAULT_SEATS}, got {seats}")
    large = 6 if seats >= LARGE_ROOM_SEATS else 0
    fours, remainder = divmod(seats - large, 4)
    twos = -(-remainder // 2)
    sizes = [4] * fours + [2] * twos + ([large] if large else [])
    return [TableSpec(label=f"T{number}", seats=size) for number, size in enumerate(sizes, 1)]


def generated_tools(profile: MerchantProfile) -> list[GeneratedTool]:
    """The agent's tools as shown on the profile card, from the template's descriptions."""
    city = profile.location.city if profile.location else None
    values = {
        "name": profile.name,
        "city": city or "unknown city",
        "services": ", ".join(f"{service.start}-{service.end}" for service in profile.services),
        "max_party_size": profile.max_party_size,
        "cancellation_policy": profile.cancellation_policy or "none stated",
    }
    return [
        GeneratedTool(name=tool.name, description=tool.description.format_map(values))
        for tool in TEMPLATE.tools
    ]


def _registry_row(profile: MerchantProfile) -> dict[str, Any]:
    return {
        "slug": profile.slug,
        "name": profile.name,
        "website": str(profile.website),
        "fallback_url": str(profile.fallback_url),
        "city": profile.location.city if profile.location else None,
        "tags": list(profile.tags),
        "price_level": profile.price_level,
        "profile": profile.model_dump(mode="json"),
    }


async def provision(engine: AsyncEngine, profile: MerchantProfile) -> MerchantRecord:
    """Create the merchant's schema, floor plan, policy and registry row, all or nothing.

    Postgres DDL is transactional, so a failure at any step leaves nothing behind. The
    registry row starts in `building`; `deploy` makes the merchant discoverable. Raises
    `SlugTaken` if the slug is in use, including by a concurrent `provision`.

    The merchant's `tables` and `policy` rows are the source of truth afterwards. The
    registry's `profile` column is a copy for display: its `seats` may disagree with the
    tables, and `update_policy` / `update_tables` must update it too (#9).
    """
    schema = schema_for(profile.slug)
    async with merchant_conn(engine, profile.slug) as conn:
        existing = await conn.scalar(
            select(merchants_t.c.id).where(merchants_t.c.slug == profile.slug)
        )
        if existing is not None:
            raise SlugTaken(f"Merchant {profile.slug!r} is already provisioned")
        try:
            await conn.execute(CreateSchema(schema))
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) not in SCHEMA_TAKEN:
                raise
            raise SlugTaken(f"Merchant {profile.slug!r} is already provisioned") from error
        await conn.run_sync(merchant_metadata.create_all)
        await conn.execute(
            insert(tables_t),
            [{"label": table.label, "seats": table.seats} for table in profile.tables],
        )
        await conn.execute(
            insert(policy_t).values(
                max_party_size=profile.max_party_size,
                slot_length_min=TEMPLATE.defaults.slot_length_min,
                services=[service.model_dump() for service in profile.services],
            )
        )
        merchant_id = await conn.scalar(
            insert(merchants_t).values(_registry_row(profile)).returning(merchants_t.c.id)
        )
    return MerchantRecord(id=merchant_id, slug=profile.slug, schema=schema, status="building")


async def set_status(engine: AsyncEngine, slug: str, status: SwitchableStatus) -> None:
    """Switch a provisioned merchant on (`live`) or off (`down`: the demo's outage switch)."""
    if status not in get_args(SwitchableStatus):
        raise InvalidInput(f"Status must be one of {get_args(SwitchableStatus)}, got {status!r}")
    async with engine.begin() as conn:
        result = await conn.execute(
            update(merchants_t).where(merchants_t.c.slug == slug).values(status=status)
        )
    if result.rowcount == 0:
        raise NotFound(f"Unknown merchant: {slug!r}")


async def deploy(engine: AsyncEngine, slug: str) -> None:
    """Last builder step: the merchant becomes discoverable by the gateway."""
    await set_status(engine, slug, "live")


async def deprovision(engine: AsyncEngine, slug: str) -> bool:
    """Drop the merchant's schema and registry row (its guest notices cascade).

    Idempotent, for `demo-reset` and tests: returns False when there was nothing to remove.
    """
    schema = schema_for(slug)
    async with engine.begin() as conn:
        had_schema = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_schema(schema))
        await conn.execute(DropSchema(schema, cascade=True, if_exists=True))
        result = await conn.execute(delete(merchants_t).where(merchants_t.c.slug == slug))
    return had_schema or result.rowcount > 0
