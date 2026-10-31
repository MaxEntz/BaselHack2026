"""`GET /merchants/{slug}` (#129, #132): what the management site shows about a merchant."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from builder.build import gateway_mcp_url
from core.config import Settings
from core.db import merchants_t
from core.models import Look, MerchantOverview, MerchantStatus, PriceLevel, Tag

# The fictional restaurants of `code/sites/`; a rebuild of one may take a `-2` suffix (#22).
DEMO_SLUGS = ("le-petit-zinc", "la-graine-verte", "maison-albane")


@dataclass(frozen=True)
class MerchantRecord:
    """A merchant's registry row, as the overview reads it."""

    slug: str
    name: str
    city: str | None
    price_level: PriceLevel
    tags: tuple[Tag, ...]
    website: str
    status: MerchantStatus
    look: Look | None = None


RegistryLookup = Callable[[str], Awaitable[MerchantRecord | None]]


def registry_records(engine: AsyncEngine) -> RegistryLookup:
    columns = merchants_t.c

    async def lookup(slug: str) -> MerchantRecord | None:
        query = select(
            columns.slug,
            columns.name,
            columns.city,
            columns.price_level,
            columns.tags,
            columns.website,
            columns.status,
            columns.profile,
        ).where(columns.slug == slug)
        async with engine.connect() as conn:
            row = (await conn.execute(query)).one_or_none()
        if row is None:
            return None
        return MerchantRecord(
            row.slug,
            row.name,
            row.city,
            row.price_level,
            tuple(row.tags),
            row.website,
            row.status,
            stored_look(row.profile),
        )

    return lookup


def stored_look(profile: Any) -> Look | None:
    """The stored profile's look (#149); None for a profile stored before it, or unreadable:
    the management site then uses the default look."""
    look = profile.get("look") if isinstance(profile, dict) else None
    try:
        return Look.model_validate(look) if look is not None else None
    except ValidationError:
        return None


def is_demo(slug: str) -> bool:
    return any(slug == demo or slug.startswith(f"{demo}-") for demo in DEMO_SLUGS)


def merchant_overview(
    record: MerchantRecord, owner_name: str | None, settings: Settings, now: datetime
) -> MerchantOverview:
    mcp = gateway_mcp_url(settings.gateway_public_url)
    host = urlsplit(mcp).netloc
    web = settings.platform_web_url.rstrip("/")
    return MerchantOverview(
        slug=record.slug,
        name=record.name,
        city=record.city,
        price_level=record.price_level,
        tags=record.tags,
        website=record.website,
        status=record.status,
        timezone=settings.merchant_tz,
        now=now.astimezone(ZoneInfo(settings.merchant_tz)),
        # The node's own address (ADR-0011): loopback only for now, shown as the product's.
        agent_address=f"batp://{host}/{record.slug}",
        mcp_url=mcp.removesuffix("/mcp") + f"/m/{record.slug}/mcp",
        management_url=f"{web}/manage/{record.slug}",
        owner_name=owner_name,
        demo=is_demo(record.slug),
        look=record.look,
    )
