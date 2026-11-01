"""The management site's forms (#133): opening hours, floor plan, rules, settings, blocked times.

Each read answers what its form saves back; each save keeps the registry's columns and
`profile` in step with the merchant's rows, in the one transaction of `merchant_conn`, and
writes one activity line when something changed. The hours and the floor plan save through
`agent.admin` (`update_policy`, `update_tables`), as the platform assistant's tools do.
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from agent import activity
from agent.admin import update_profile
from agent.provisioning import described_tools, one_line
from agent.times import day_bounds, hhmm
from agent.writes import not_a_start
from core.availability import bookable_starts
from core.db import AnyRow, blocked_slots_t, merchants_t, policy_t, tables_t
from core.errors import FormProblem, InvalidInput, NotFound
from core.forms import check_party_limit
from core.models import (
    MAX_POLICY_TEXT,
    BlockedTime,
    MerchantProfile,
    MerchantSettings,
    Policy,
    PriceLevel,
    Problem,
    Rules,
    Service,
    TableSpec,
    Tag,
)


async def get_policy(conn: AsyncConnection) -> Policy:
    row = (await conn.execute(select(policy_t))).one()
    return Policy(
        max_party_size=row.max_party_size,
        slot_length_min=row.slot_length_min,
        services=tuple(
            sorted((Service.model_validate(s) for s in row.services), key=lambda s: s.start)
        ),
    )


async def get_tables(conn: AsyncConnection, slug: str) -> list[TableSpec]:
    """The floor plan in the order it was last saved (the registry's copy keeps it), so a
    form shows its rows where the merchant put them; a table missing from it comes last."""
    profile = await _profile(conn, slug)
    order = {t.get("label"): i for i, t in enumerate(profile.get("tables") or [])}
    rows = (
        await conn.execute(select(tables_t.c.label, tables_t.c.seats).order_by(tables_t.c.id))
    ).all()
    ordered = sorted(rows, key=lambda row: order.get(row.label, len(order)))
    return [TableSpec(label=row.label, seats=row.seats) for row in ordered]


async def get_rules(conn: AsyncConnection, slug: str) -> Rules:
    registry = await _registry(conn, slug)
    policy = await get_policy(conn)
    stored = registry.profile.get("cancellation_policy")
    # The builder keeps the site's text: cut to what `RulesInput` takes back, one paragraph.
    paragraph = one_line(stored, MAX_POLICY_TEXT) if isinstance(stored, str) else None
    profile = MerchantProfile.model_validate(
        {
            **registry.profile,
            "max_party_size": policy.max_party_size,
            "services": [s.model_dump() for s in policy.services],
        }
    )
    return Rules(
        max_party_size=policy.max_party_size,
        slot_length_min=policy.slot_length_min,
        fallback_url=registry.fallback_url,
        cancellation_policy=paragraph or None,
        tools=tuple(described_tools(profile)),
    )


async def update_rules(
    conn: AsyncConnection,
    slug: str,
    max_party_size: int,
    fallback_url: str,
    cancellation_policy: str | None,
    *,
    at: datetime | None = None,
) -> Rules:
    """The group limit, the booking page guests are sent to, and the cancellation policy.

    The booking page stays on the merchant's website, as the builder's (#22): guests only see
    it when the agent has failed, so it must be the restaurant's own.
    """
    check_party_limit(max_party_size)
    registry = await _registry(conn, slug, lock=True)
    if not _same_site(fallback_url, registry.website):
        raise FormProblem(
            "The booking page stays on your website.", Problem(code="booking_page_off_site")
        )
    stored = registry.profile.get("cancellation_policy")
    if isinstance(stored, str) and not one_line(stored):
        stored = None  # as `get_rules` shows a blank policy
    policy = cancellation_policy or None
    if isinstance(stored, str) and policy == one_line(stored, MAX_POLICY_TEXT):
        # The form got the stored text cut to one paragraph (`get_rules`): sent back as it
        # was served, the policy keeps its full text (#201).
        policy = stored
    before = (await conn.scalar(select(policy_t.c.max_party_size)), registry.fallback_url, stored)
    await conn.execute(update(policy_t).values(max_party_size=max_party_size))
    await conn.execute(
        update(merchants_t).where(merchants_t.c.slug == slug).values(fallback_url=fallback_url)
    )
    await update_profile(
        conn,
        slug,
        {
            "max_party_size": max_party_size,
            "fallback_url": fallback_url,
            "cancellation_policy": policy,
        },
    )
    if before != (max_party_size, fallback_url, policy):
        await activity.record(
            conn, slug, "saved", "merchant", details={"what": activity.BOOKING_RULES}, at=at
        )
    return await get_rules(conn, slug)


async def get_settings(conn: AsyncConnection, slug: str) -> MerchantSettings:
    registry = await _registry(conn, slug)
    return MerchantSettings(
        name=registry.name,
        city=registry.city,
        website=registry.website,
        price_level=registry.price_level,
        tags=tuple(registry.tags),
    )


async def update_settings(
    conn: AsyncConnection,
    slug: str,
    name: str,
    city: str | None,
    price_level: PriceLevel,
    tags: Sequence[Tag],
    *,
    at: datetime | None = None,
) -> MerchantSettings:
    """What discovery and the tool descriptions show of the merchant: its name, city, price
    and tags. The website stays: it is the agent's identity (one agent per site, #22)."""
    if not name.strip():
        raise FormProblem("The restaurant needs a name.", Problem(code="name_required"))
    registry = await _registry(conn, slug, lock=True)
    tags = list(dict.fromkeys(tags))
    city = city or None
    before = (registry.name, registry.city, registry.price_level, list(registry.tags))
    await conn.execute(
        update(merchants_t)
        .where(merchants_t.c.slug == slug)
        .values(name=name, city=city, price_level=price_level, tags=tags)
    )
    location = {**(registry.profile.get("location") or {}), "city": city}
    await update_profile(
        conn,
        slug,
        {"name": name, "location": location, "price_level": price_level, "tags": tags},
    )
    if before != (name, city, price_level, tags):
        await activity.record(
            conn, slug, "saved", "merchant", details={"what": activity.SETTINGS}, at=at
        )
    return await get_settings(conn, slug)


async def list_blocked(conn: AsyncConnection, now: datetime, tz: ZoneInfo) -> list[BlockedTime]:
    """The blocked starts to come, by time."""
    rows = await conn.scalars(
        select(blocked_slots_t.c.starts_at)
        .where(blocked_slots_t.c.starts_at > now)
        .order_by(blocked_slots_t.c.starts_at)
    )
    local = [moment.astimezone(tz) for moment in rows]
    return [
        BlockedTime(starts_at=moment, date=moment.date(), time=hhmm(moment)) for moment in local
    ]


async def set_blocked(
    conn: AsyncConnection,
    slug: str,
    services: Sequence[Service],
    day: date,
    times: Sequence[str],
    tz: ZoneInfo,
    *,
    now: datetime,
    at: datetime | None = None,
) -> None:
    """The day's blocked starts still to come become `times` (local `HH:MM`): the last
    write wins (#83).

    Every time must be a start of the day. Only starts after `now` change: the form only knows
    those (`list_blocked`), so the day's past blocks and its history stay, and a past time sent
    is ignored. One activity line for the whole save: the times it blocked, else the ones it
    reopened. Bookings already made stay (AGENTS.md §6).
    """
    starts = {hhmm(moment, tz): moment for moment in bookable_starts(services, day, tz)}
    unknown = sorted(set(times) - starts.keys())
    if unknown:
        raise InvalidInput(not_a_start(", ".join(unknown), day, list(starts)))
    wanted = [starts[time] for time in sorted(set(times)) if starts[time] > now]
    start, end = day_bounds(day, tz)
    in_day = (
        blocked_slots_t.c.starts_at >= start,
        blocked_slots_t.c.starts_at > now,
        blocked_slots_t.c.starts_at < end,
    )
    removed = list(
        await conn.scalars(
            delete(blocked_slots_t)
            .where(*in_day, blocked_slots_t.c.starts_at.not_in(wanted))
            .returning(blocked_slots_t.c.starts_at)
        )
    )
    added: list[datetime] = []
    if wanted:
        added = list(
            await conn.scalars(
                pg_insert(blocked_slots_t)
                .values([{"starts_at": moment} for moment in wanted])
                .on_conflict_do_nothing()
                .returning(blocked_slots_t.c.starts_at)
            )
        )
    if added:
        await activity.record_times(conn, slug, "blocked", added, at=at)
    elif removed:
        await activity.record_times(conn, slug, "reopened", removed, at=at)


def _same_site(url: str, website: str) -> bool:
    """Same host, `www.` aside, and same port: the demo sites differ only by their port."""

    def site(value: str) -> tuple[str, int | None]:
        parts = urlsplit(value)
        try:
            port = parts.port or {"http": 80, "https": 443}.get(parts.scheme)
        except ValueError:
            return "", None
        return (parts.hostname or "").removeprefix("www."), port

    return bool(site(url)[0]) and site(url) == site(website)


async def _registry(conn: AsyncConnection, slug: str, *, lock: bool = False) -> AnyRow:
    """The merchant's registry row; `lock` serializes the forms that save it."""
    query = select(merchants_t).where(merchants_t.c.slug == slug)
    if lock:
        query = query.with_for_update(key_share=True)
    row = (await conn.execute(query)).one_or_none()
    if row is None:
        raise NotFound(f"Unknown merchant: {slug!r}")
    return row


async def _profile(conn: AsyncConnection, slug: str) -> dict[str, Any]:
    profile = await conn.scalar(select(merchants_t.c.profile).where(merchants_t.c.slug == slug))
    if profile is None:
        raise NotFound(f"Unknown merchant: {slug!r}")
    return dict(profile)
