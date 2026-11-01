"""MerchantProfile -> merchant schema, tables, policy and registry row.

Deterministic code driven by the validated profile and `templates/restaurant.json`: the LLM
only fills the profile, it never produces SQL, code or tool definitions (AGENTS.md §7.1).

The database functions take an optional `engine`: long-running processes pass their own;
scripts omit it and get a short-lived engine on DATABASE_URL, disposed after the call.
"""

import unicodedata
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from string import Formatter
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, PositiveInt, ValidationError, model_validator
from sqlalchemy import delete, insert, inspect, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.schema import CreateSchema, DropSchema

from core.db import (
    make_engine,
    merchant_conn,
    merchant_metadata,
    merchants_t,
    policy_t,
    schema_for,
    tables_t,
)
from core.errors import InvalidInput, NotFound
from core.models import (
    PUBLISHED_STATUSES,
    AgentStatus,
    GeneratedTool,
    MerchantProfile,
    MerchantStatus,
    PriceLevel,
    Services,
    TableSpec,
)

# Values a tool description may use, with sample values to render it once at import.
PLACEHOLDER_SAMPLES: dict[str, Any] = {
    "name": "Le Petit Zinc",
    "city": "Mulhouse",
    "services": "19:00-22:00",
    "max_party_size": 8,
    "cancellation_policy": "none stated",
}

# Merchant free text allowed in a tool description (name, city).
MAX_FREE_TEXT = 80
# Instead of the merchant's own words, which could carry instructions for Claude.
POLICY_ELSEWHERE = "see the restaurant's own website"

# Postgres errors raised by CREATE SCHEMA when the name is taken: duplicate_schema, or a
# unique violation on pg_namespace when a concurrent transaction creates it first.
SCHEMA_TAKEN = frozenset({"42P06", "23505"})

# `seats` may come from the LLM: refuse a plan nobody would believe in a demo.
MAX_DEFAULT_SEATS = 400

# From this size, the default floor plan includes one 6-seat table.
LARGE_ROOM_SEATS = 30


class SlugTaken(InvalidInput):
    """The slug already has a merchant (or a leftover schema): the builder picks another."""


class NotBuilding(InvalidInput):
    """`deploy` found the merchant published already: `live`, or `down` (paused by its owner).

    Nothing changed: `status` is what the registry holds."""

    def __init__(self, slug: str, status: MerchantStatus) -> None:
        super().__init__(f"Merchant {slug!r} isn't waiting to be published: it is {status}")
        self.status = status


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
# The tool Claude reads instead of the 4 booking tools, and what its owner reads about it.
TALK_TOOL = "talk_to_merchant"
TALK_SHOWN = (
    "It answers in natural language from what your site says, and books, changes or cancels only "
    "after the guest's yes."
)


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
    status: MerchantStatus


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
        # The template ends its sentence.
        "cancellation_policy": (profile.cancellation_policy or "none stated").rstrip("."),
    }
    return [
        GeneratedTool(name=tool.name, description=tool.description.format_map(values))
        for tool in TEMPLATE.tools
    ]


def one_line(text: str, limit: int = MAX_FREE_TEXT) -> str:
    """Merchant text fit for a tool description: one line, no control characters, bounded."""
    printable = "".join(" " if unicodedata.category(char)[0] == "C" else char for char in text)
    line = " ".join(printable.split())
    return line if len(line) <= limit else f"{line[: limit - 1].rstrip()}…"


def described_profile(profile: MerchantProfile) -> MerchantProfile:
    """The profile as the descriptions may show it.

    Tool descriptions read as the connector's instructions, and the free text of a profile comes
    from the merchant's website, which anyone can onboard (#22): the cancellation policy never
    goes in, and the name and city are bounded. Services and party size are validated already.
    """
    location = profile.location
    if location and location.city:
        location = location.model_copy(update={"city": one_line(location.city)})
    return profile.model_copy(
        update={
            "name": one_line(profile.name),
            "location": location,
            "cancellation_policy": POLICY_ELSEWHERE,
        }
    )


def talk_sentence(profile: MerchantProfile) -> str:
    """How `talk_to_merchant` opens, from the described profile: no merchant free text (#22)."""
    shown = described_profile(profile)
    city = shown.location.city if shown.location else None
    return f"Talk to the agent of {shown.name}{f' ({city})' if city else ''}."


def tools_agents_read(profile: MerchantProfile, *, talk: bool) -> list[GeneratedTool]:
    """What agents read of this merchant, as its owner sees it (#253): the one tool while the
    gateway offers the merchant's agent (`GATEWAY_TALK`, #251), else the 4 booking tools."""
    if talk:
        return [GeneratedTool(name=TALK_TOOL, description=f"{talk_sentence(profile)} {TALK_SHOWN}")]
    return generated_tools(profile)


def described_tools(profile: MerchantProfile) -> list[GeneratedTool]:
    """What an AI agent reads on `/m/<slug>/mcp`: the tool sentences of the described profile."""
    return generated_tools(described_profile(profile))


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


@asynccontextmanager
async def _engine(engine: AsyncEngine | None) -> AsyncIterator[AsyncEngine]:
    """The caller's engine, or a short-lived one on DATABASE_URL, disposed afterwards."""
    if engine is not None:
        yield engine
        return
    owned = make_engine()
    try:
        yield owned
    finally:
        await owned.dispose()


async def provision(
    profile: MerchantProfile, *, engine: AsyncEngine | None = None
) -> MerchantRecord:
    """Create the merchant's schema, floor plan, policy and registry row, all or nothing.

    Postgres DDL is transactional, so a failure at any step leaves nothing behind. The
    registry row starts in `building`; `deploy` makes the merchant discoverable. Raises
    `SlugTaken` if the slug is in use, including by a concurrent `provision`.

    The merchant's `tables` and `policy` rows are the source of truth afterwards. The
    registry's `profile` column is a copy for display: its `seats` may disagree with the
    tables, and `update_policy` / `update_tables` must update it too (#9).
    """
    schema = schema_for(profile.slug)
    # By start, as `update_policy` stores them, so the tool descriptions word them alike (#201).
    services = tuple(sorted(profile.services, key=lambda s: s.start))
    profile = profile.model_copy(update={"services": services})
    async with _engine(engine) as db, merchant_conn(db, profile.slug) as conn:
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


async def set_status(slug: str, status: AgentStatus, *, engine: AsyncEngine | None = None) -> None:
    """Switch a provisioned merchant on (`live`) or off (`down`: the demo's outage switch).

    For scripts and tests (`make outage`, `make restore`, the seed): any provisioned merchant,
    `building` included, and no activity line. The merchant's own pause is
    `agent.admin.pause_or_resume` (`AgentService.set_status`): only a published agent, recorded
    on Home.
    """
    if status not in PUBLISHED_STATUSES:
        raise InvalidInput(f"Status must be one of {PUBLISHED_STATUSES}, got {status!r}")
    async with _engine(engine) as db, db.begin() as conn:
        result = await conn.execute(
            update(merchants_t).where(merchants_t.c.slug == slug).values(status=status)
        )
    if result.rowcount == 0:
        raise NotFound(f"Unknown merchant: {slug!r}")


async def deploy(slug: str, *, engine: AsyncEngine | None = None) -> None:
    """Last builder step: a `building` merchant becomes `live`, discoverable by the gateway.

    One conditional UPDATE, so that a merchant published and paused since the caller read its
    status stays `down` (#220). Raises `NotBuilding` with the status found when it is no longer
    `building`, `NotFound` for an unknown merchant. `set_status` switches a published one.
    """
    async with _engine(engine) as db, db.begin() as conn:
        result = await conn.execute(
            update(merchants_t)
            .where(merchants_t.c.slug == slug, merchants_t.c.status == "building")
            .values(status="live")
        )
        if result.rowcount == 1:
            return
        status: MerchantStatus | None = await conn.scalar(
            select(merchants_t.c.status).where(merchants_t.c.slug == slug)
        )
    if status is None:
        raise NotFound(f"Unknown merchant: {slug!r}")
    raise NotBuilding(slug, status)


async def deprovision(slug: str, *, engine: AsyncEngine | None = None) -> bool:
    """Drop the merchant's schema and registry row (its guest notices cascade).

    Idempotent, for `demo-reset` and tests: returns False when there was nothing to remove.
    """
    schema = schema_for(slug)
    async with _engine(engine) as db, db.begin() as conn:
        had_schema = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_schema(schema))
        await conn.execute(DropSchema(schema, cascade=True, if_exists=True))
        result = await conn.execute(delete(merchants_t).where(merchants_t.c.slug == slug))
    return had_schema or result.rowcount > 0
