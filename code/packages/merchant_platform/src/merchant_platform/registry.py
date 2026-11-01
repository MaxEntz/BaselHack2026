"""The merchants' registry row (`public.merchants`), as the platform reads and writes it.

One query reads the whole row (`registry_records`): the assistant reads the profile and the
status from it before every model call, the admin tools the status and the look, and
`GET /merchants/{slug}` the rest.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from core.db import merchants_t
from core.models import Look, MerchantProfile, MerchantStatus, PriceLevel, Tag


@dataclass(frozen=True)
class MerchantRecord:
    """A merchant's registry row."""

    slug: str
    name: str
    city: str | None
    price_level: PriceLevel
    tags: tuple[Tag, ...]
    website: str
    status: MerchantStatus
    look: Look | None = None
    # The `profile` column as stored: the display copy of the merchant's profile (§7.2).
    stored_profile: Any = None

    def profile(self) -> MerchantProfile | None:
        """The stored profile, validated; `ValidationError` if it isn't one."""
        if self.stored_profile is None:
            return None
        return MerchantProfile.model_validate(self.stored_profile)


RegistryLookup = Callable[[str], Awaitable[MerchantRecord | None]]
ProfileLookup = Callable[[str], Awaitable[MerchantProfile | None]]
SaveLook = Callable[[str, Look], Awaitable[None]]


def registry_records(engine: AsyncEngine) -> RegistryLookup:
    """The merchant's row, or None for an unknown merchant."""
    columns = merchants_t.c
    query = select(
        columns.slug,
        columns.name,
        columns.city,
        columns.price_level,
        columns.tags,
        columns.website,
        columns.status,
        columns.profile,
    )

    async def lookup(slug: str) -> MerchantRecord | None:
        async with engine.connect() as conn:
            row = (await conn.execute(query.where(columns.slug == slug))).one_or_none()
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
            row.profile,
        )

    return lookup


def registry_profiles(engine: AsyncEngine) -> ProfileLookup:
    """The validated profile stored in the registry, or None for an unknown merchant."""

    async def lookup(slug: str) -> MerchantProfile | None:
        async with engine.connect() as conn:
            stored = await conn.scalar(
                select(merchants_t.c.profile).where(merchants_t.c.slug == slug)
            )
        return None if stored is None else MerchantProfile.model_validate(stored)

    return lookup


def look_store(engine: AsyncEngine) -> SaveLook:
    """Saves the management site's look into the registry's stored profile (#152): the
    profile's display copy, which `GET /merchants/{slug}` reads; no merchant schema change."""

    async def save(slug: str, look: Look) -> None:
        columns = merchants_t.c
        async with engine.begin() as conn:
            profile = await conn.scalar(select(columns.profile).where(columns.slug == slug))
            if not isinstance(profile, dict):
                return
            stored = {**profile, "look": look.model_dump(mode="json")}
            await conn.execute(
                merchants_t.update().where(columns.slug == slug).values(profile=stored)
            )

    return save


def stored_look(profile: Any) -> Look | None:
    """The stored profile's look (#149); None for a profile stored before it, or unreadable:
    the management site then uses the default look."""
    look = profile.get("look") if isinstance(profile, dict) else None
    try:
        return Look.model_validate(look) if look is not None else None
    except ValidationError:
        return None
