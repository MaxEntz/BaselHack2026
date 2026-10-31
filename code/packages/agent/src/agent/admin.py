"""Merchant-side operations of `PgAgentService` (#9).

Reservations list, stats, slot blocking, policy and floor-plan changes, the management site's
forms (#133: rules, settings, a day's blocked times), and merchant cancellation with a guest
notice. Only the platform assistant and the management site call them, never Claude users
(AGENTS.md §8.2). Each function runs on a connection from `merchant_conn`, so whatever one
call changes, in the merchant's schema and in the registry, commits or rolls back together.

None of them moves a booking (`during`, `party_size`, `table_id`): the live events treat
every reservation update as the guest's (`merchant_platform/events.py`, #81). A merchant-side
move must also tell the listener who made it, or the merchant's chat will report it as the
guest's.
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Row, delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from agent import activity
from agent.guests import guest_key
from agent.provisioning import described_tools, one_line
from agent.requests import describe
from core import forms
from core.availability import bookable_starts, is_bookable_start
from core.db import (
    agent_requests_t,
    blocked_slots_t,
    guest_notices_t,
    merchants_t,
    policy_t,
    reservations_t,
    tables_t,
)
from core.errors import FormProblem, InvalidInput, NotFound
from core.models import (
    MAX_POLICY_TEXT,
    ActivityKind,
    AdminReservationRow,
    AgentRequest,
    BlockedTime,
    FloorPlan,
    GuestRow,
    MerchantCancelOutcome,
    MerchantProfile,
    MerchantSettings,
    Policy,
    PriceLevel,
    Problem,
    Rules,
    Service,
    Services,
    Stats,
    TableSpec,
    Tag,
)

# Any row shape (SQLAlchemy 2.1 rows are variadic generics).
_Row = Row[*tuple[Any, ...]]

# What the Agent page shows of a day (`AgentRequests`).
MAX_AGENT_REQUESTS = 10

# Bounds for merchant input, well inside Postgres `integer`: a typo never reaches the DB.
MAX_SEATS = forms.MAX_SEATS
# The Guests page covers today and the 6 days after it.
DAYS_AHEAD = 7

_SERVICES = TypeAdapter(Services)
_FLOOR_PLAN = TypeAdapter(FloorPlan)

# SQL expression for a booking's start (lower bound of `during`).
_start_of = func.lower(reservations_t.c.during)


def _day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """The merchant's local day, [00:00, next 00:00): a 23:30 booking stays on its day."""
    try:
        next_day = day + timedelta(days=1)
    except OverflowError:
        raise InvalidInput(f"Date out of range: {day}") from None
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    return start, datetime.combine(next_day, datetime.min.time(), tzinfo=tz)


def _hhmm(moment: datetime, tz: ZoneInfo) -> str:
    return f"{moment.astimezone(tz):%H:%M}"


async def list_reservations(
    conn: AsyncConnection, day: date, tz: ZoneInfo, *, include_cancelled: bool = False
) -> list[AdminReservationRow]:
    """The day's bookings by start time; cancelled ones only on request."""
    start, end = _day_bounds(day, tz)
    query = (
        select(
            reservations_t.c.id,
            _start_of.label("starts_at"),
            reservations_t.c.guest_name,
            reservations_t.c.party_size,
            tables_t.c.label,
            reservations_t.c.via,
            reservations_t.c.status,
        )
        .join(tables_t, tables_t.c.id == reservations_t.c.table_id)
        .where(_start_of >= start, _start_of < end)
        .order_by(_start_of, reservations_t.c.id)
    )
    if not include_cancelled:
        query = query.where(reservations_t.c.status == "confirmed")
    return [
        AdminReservationRow(
            id=row.id,
            time=_hhmm(row.starts_at, tz),
            guest_name=row.guest_name,
            party_size=row.party_size,
            table_label=row.label,
            via=row.via,
            status=row.status,
        )
        for row in await conn.execute(query)
    ]


async def get_stats(conn: AsyncConnection, day: date, tz: ZoneInfo) -> Stats:
    """Confirmed bookings and guests that day, total capacity, the day's blocked starts."""
    bookings = await list_reservations(conn, day, tz)
    seats = await conn.scalar(select(func.coalesce(func.sum(tables_t.c.seats), 0)))
    start, end = _day_bounds(day, tz)
    blocked = await conn.scalars(
        select(blocked_slots_t.c.starts_at)
        .where(blocked_slots_t.c.starts_at >= start, blocked_slots_t.c.starts_at < end)
        .order_by(blocked_slots_t.c.starts_at)
    )
    return Stats(
        bookings=len(bookings),
        guests=sum(row.party_size for row in bookings),
        seats=seats or 0,
        blocked=tuple(_hhmm(moment, tz) for moment in blocked),
    )


async def list_guests(
    conn: AsyncConnection, query: str, now: datetime, tz: ZoneInfo
) -> list[GuestRow]:
    """Guests with a live booking that hasn't started, from now to the end of the 7th day,
    by name (#144). One row per `guest_key`, kept if `query` is part of it."""
    _, end = _day_bounds(now.astimezone(tz).date() + timedelta(days=DAYS_AHEAD - 1), tz)
    rows = await conn.execute(
        select(
            reservations_t.c.id,
            _start_of.label("starts_at"),
            reservations_t.c.guest_name,
            reservations_t.c.party_size,
            reservations_t.c.via,
        )
        .where(reservations_t.c.status == "confirmed", _start_of > now, _start_of < end)
        .order_by(_start_of, reservations_t.c.id)
    )
    wanted = guest_key(query)
    by_guest: dict[str, list[_Row]] = {}
    for row in rows:
        key = guest_key(row.guest_name)
        if wanted in key:
            by_guest.setdefault(key, []).append(row)
    return [
        GuestRow(
            guest_name=visits[0].guest_name,
            reservation_id=visits[0].id,
            date=visits[0].starts_at.astimezone(tz).date(),
            time=_hhmm(visits[0].starts_at, tz),
            party_size=visits[0].party_size,
            bookings=len(visits),
            via=visits[0].via,
        )
        for _, visits in sorted(by_guest.items())
    ]


async def block_slot(
    conn: AsyncConnection,
    slug: str,
    services: Sequence[Service],
    starts_at: datetime,
    tz: ZoneInfo,
    *,
    at: datetime | None = None,
) -> None:
    """No new booking may start at `starts_at`. Existing bookings stay (AGENTS.md §6)."""
    if not is_bookable_start(services, starts_at, tz):
        local = starts_at.astimezone(tz)
        valid = ", ".join(_hhmm(s, tz) for s in bookable_starts(services, local.date(), tz))
        raise InvalidInput(
            f"{local:%H:%M} is not a start time on {local:%Y-%m-%d}; valid times: {valid or 'none'}"
        )
    added = await conn.scalar(
        pg_insert(blocked_slots_t)
        .values(starts_at=starts_at)
        .on_conflict_do_nothing()
        .returning(blocked_slots_t.c.starts_at)
    )
    if added is not None:  # already blocked: nothing changed, no line
        await activity.record_times(conn, slug, "blocked", [starts_at], at=at)


async def unblock_slot(
    conn: AsyncConnection, slug: str, starts_at: datetime, *, at: datetime | None = None
) -> None:
    removed = await conn.scalar(
        delete(blocked_slots_t)
        .where(blocked_slots_t.c.starts_at == starts_at)
        .returning(blocked_slots_t.c.starts_at)
    )
    if removed is not None:
        await activity.record_times(conn, slug, "reopened", [starts_at], at=at)


async def set_status(
    conn: AsyncConnection,
    slug: str,
    status: Literal["live", "down"],
    *,
    at: datetime | None = None,
) -> None:
    """Pause (`down`) or resume (`live`) a published agent. `down` sends other agents and Claude
    to the merchant's booking page; its website keeps booking. A `building` merchant isn't
    published yet: only `deploy` makes it `live`."""
    current = await conn.scalar(
        select(merchants_t.c.status).where(merchants_t.c.slug == slug).with_for_update()
    )
    if current not in ("live", "down"):
        raise InvalidInput("This agent isn't published yet: publish it first")
    if current == status:
        return  # paused twice: nothing changed, no line (#135)
    await conn.execute(update(merchants_t).where(merchants_t.c.slug == slug).values(status=status))
    kind: ActivityKind = "paused" if status == "down" else "resumed"
    await activity.record(conn, slug, kind, "merchant", at=at)


async def list_agent_requests(
    conn: AsyncConnection,
    slug: str,
    day: date,
    tz: ZoneInfo,
    *,
    refused: bool,
    max_party_size: int,
) -> list[AgentRequest]:
    """The latest requests other agents sent on the merchant's local `day`, newest first,
    worded for the Agent page; `refused` keeps the ones answered with a status >= 400."""
    start, end = _day_bounds(day, tz)
    t = agent_requests_t
    query = (
        select(t)
        .where(t.c.merchant_slug == slug, t.c.at >= start, t.c.at < end)
        .order_by(t.c.at.desc(), t.c.id.desc())
        .limit(MAX_AGENT_REQUESTS)
    )
    if refused:
        query = query.where(t.c.status >= 400)
    found = []
    for row in await conn.execute(query):
        title, text = describe(
            row.agent_name,
            row.intent,
            row.status,
            row.request,
            row.response,
            max_party_size=max_party_size,
            tz=tz,
        )
        found.append(
            AgentRequest(
                id=row.id,
                at=row.at.astimezone(tz),
                agent_name=row.agent_name,
                intent=row.intent,
                status=row.status,
                refused=row.status >= 400,
                title=title,
                text=text,
                request=row.request,
                response=row.response,
            )
        )
    return found


async def _update_profile(conn: AsyncConnection, slug: str, changes: dict[str, Any]) -> None:
    """Keep the registry's display copy of the profile in step with the merchant's rows."""
    # FOR NO KEY UPDATE: serializes profile writers without blocking the foreign-key checks
    # of guest notices inserted concurrently.
    profile = await conn.scalar(
        select(merchants_t.c.profile)
        .where(merchants_t.c.slug == slug)
        .with_for_update(key_share=True)
    )
    if profile is None:
        raise NotFound(f"Unknown merchant: {slug!r}")
    await conn.execute(
        update(merchants_t).where(merchants_t.c.slug == slug).values(profile={**profile, **changes})
    )


async def update_policy(
    conn: AsyncConnection,
    slug: str,
    max_party_size: int | None,
    services: Sequence[Service] | None,
    *,
    at: datetime | None = None,
) -> Policy:
    """Change the group limit and/or the services. Existing bookings are never cancelled."""
    if max_party_size is None and services is None:
        raise InvalidInput("Provide max_party_size or services")
    if max_party_size is not None and not 1 <= max_party_size <= MAX_SEATS:
        raise InvalidInput(
            f"max_party_size must be between 1 and {MAX_SEATS}, got {max_party_size}"
        )
    values: dict[str, Any] = {}
    if max_party_size is not None:
        values["max_party_size"] = max_party_size
    if services is not None:
        try:
            checked = _SERVICES.validate_python(tuple(services))
        except ValidationError as error:
            raise InvalidInput(f"Invalid services: {_reasons(error)}") from None
        values["services"] = [service.model_dump() for service in checked]
    before = (await conn.execute(select(policy_t.c.max_party_size, policy_t.c.services))).one()
    row = (await conn.execute(update(policy_t).values(values).returning(policy_t))).one()
    await _update_profile(conn, slug, values)
    # One line per section that changed: an identical save, or a retry, writes none (#135).
    if "max_party_size" in values and values["max_party_size"] != before.max_party_size:
        await activity.record(
            conn, slug, "saved", "merchant", details={"what": activity.BOOKING_RULES}, at=at
        )
    if "services" in values and values["services"] != before.services:
        await activity.record(
            conn, slug, "saved", "merchant", details={"what": activity.OPENING_HOURS}, at=at
        )
    return Policy(
        max_party_size=row.max_party_size,
        slot_length_min=row.slot_length_min,
        services=tuple(Service.model_validate(s) for s in row.services),
    )


async def update_tables(
    conn: AsyncConnection, slug: str, tables: Sequence[TableSpec], now: datetime, tz: ZoneInfo
) -> list[TableSpec]:
    """Replace the floor plan, matching tables by label.

    Refused, with nothing changed, when it would strand a booking: a removed table that
    holds an upcoming booking, or a kept table shrunk below one. A removed table with past
    or cancelled bookings is refused too, as their history still points to it.
    """
    try:
        plan = list(_FLOOR_PLAN.validate_python(tuple(tables)))
    except ValidationError as error:
        raise InvalidInput(f"Invalid floor plan: {_reasons(error)}") from None
    for table in plan:
        if not table.label.isprintable() or table.seats > MAX_SEATS:
            raise InvalidInput(
                f"Invalid table {table.label!r}: printable label and at most {MAX_SEATS} seats"
            )
    wanted = {table.label: table.seats for table in plan}
    # Locking every table, in id order, queues concurrent bookings and floor-plan changes
    # until the new plan is in place. Bookings re-check seats under their own table lock.
    current = (
        await conn.execute(
            select(tables_t.c.id, tables_t.c.label, tables_t.c.seats)
            .order_by(tables_t.c.id)
            .with_for_update()
        )
    ).all()
    await _check_plan_change(conn, current, wanted, now, tz)

    by_label = {row.label: row for row in current}
    removed = [row.id for row in current if row.label not in wanted]
    if removed:
        await conn.execute(delete(tables_t).where(tables_t.c.id.in_(removed)))
    for label, seats in wanted.items():
        if label in by_label:
            if by_label[label].seats != seats:
                await conn.execute(
                    update(tables_t).where(tables_t.c.label == label).values(seats=seats)
                )
        else:
            await conn.execute(insert(tables_t).values(label=label, seats=seats))
    await _update_profile(
        conn,
        slug,
        {
            "tables": [table.model_dump() for table in plan],
            "seats": sum(table.seats for table in plan),
        },
    )
    if sorted((t.label, t.seats) for t in plan) != sorted((t.label, t.seats) for t in current):
        await activity.record(
            conn, slug, "saved", "merchant", details={"what": activity.FLOOR_PLAN}, at=now
        )
    return plan


async def _check_plan_change(
    conn: AsyncConnection,
    current: Sequence[_Row],
    wanted: dict[str, int],
    now: datetime,
    tz: ZoneInfo,
) -> None:
    labels = {row.id: row.label for row in current}
    # Only tables that lose seats can strand a booking.
    affected = [
        row.id for row in current if row.label not in wanted or wanted[row.label] < row.seats
    ]
    if not affected:
        return
    bookings = await conn.execute(
        select(
            reservations_t.c.id,
            reservations_t.c.table_id,
            reservations_t.c.guest_name,
            reservations_t.c.party_size,
            reservations_t.c.status,
            _start_of.label("starts_at"),
            func.upper(reservations_t.c.during).label("ends_at"),
        )
        .where(reservations_t.c.table_id.in_(affected))
        .order_by(_start_of, reservations_t.c.id)
    )
    # Each refusal is also a `Problem`, the first one found, so the management site can word
    # it ("T3 has to stay, because Kim Weber's booking refers to it.", #133).
    problems: list[tuple[str, Problem]] = []
    history: dict[str, str] = {}  # removed table -> guest of its first past booking
    for booking in bookings:
        label = labels[booking.table_id]
        upcoming = booking.status == "confirmed" and booking.ends_at > now
        when = f"{booking.starts_at.astimezone(tz):%Y-%m-%d %H:%M}"
        guest = booking.guest_name
        if label not in wanted:
            if upcoming:
                problems.append(
                    (
                        f"{label} holds {booking.id} ({when})",
                        Problem(code="table_in_use", table=label, guest=guest),
                    )
                )
            else:
                history.setdefault(label, guest)
        elif upcoming and booking.party_size > wanted[label]:
            problems.append(
                (
                    f"{label} would seat {wanted[label]}, but {booking.id} ({when}) is for "
                    f"{booking.party_size}",
                    Problem(
                        code="table_too_small", table=label, guest=guest, seats=booking.party_size
                    ),
                )
            )
    if problems:
        raise FormProblem(
            "The new floor plan strands bookings: " + "; ".join(text for text, _ in problems),
            problems[0][1],
        )
    if history:
        first = sorted(history)[0]
        raise FormProblem(
            f"{', '.join(sorted(history))} cannot be removed: past bookings refer to it. "
            "Keep it in the floor plan.",
            Problem(code="table_in_use", table=first, guest=history[first]),
        )


# The management site's forms (#133): each read answers what its form saves back; each save
# keeps the registry's columns and `profile` in step with the merchant's rows, in one
# transaction, and writes one activity line when something changed.


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
    if not 1 <= max_party_size <= MAX_SEATS:
        raise InvalidInput(
            f"max_party_size must be between 1 and {MAX_SEATS}, got {max_party_size}"
        )
    registry = await _registry(conn, slug, lock=True)
    if not _same_site(fallback_url, registry.website):
        raise FormProblem(
            "The booking page stays on your website.", Problem(code="booking_page_off_site")
        )
    policy = cancellation_policy or None
    before = (
        await conn.scalar(select(policy_t.c.max_party_size)),
        registry.fallback_url,
        registry.profile.get("cancellation_policy"),
    )
    await conn.execute(update(policy_t).values(max_party_size=max_party_size))
    await conn.execute(
        update(merchants_t).where(merchants_t.c.slug == slug).values(fallback_url=fallback_url)
    )
    await _update_profile(
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
    await _update_profile(
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
        BlockedTime(starts_at=moment, date=moment.date(), time=f"{moment:%H:%M}")
        for moment in local
    ]


async def set_blocked(
    conn: AsyncConnection,
    slug: str,
    services: Sequence[Service],
    day: date,
    times: Sequence[str],
    tz: ZoneInfo,
    *,
    at: datetime | None = None,
) -> None:
    """The day's blocked starts become `times` (local `HH:MM`): the last write wins (#83).

    Every time must be a start of the day. One activity line for the whole save: the times
    it blocked, else the ones it reopened. Bookings already made stay (AGENTS.md §6).
    """
    starts = {_hhmm(moment, tz): moment for moment in bookable_starts(services, day, tz)}
    unknown = sorted(set(times) - starts.keys())
    if unknown:
        raise InvalidInput(
            f"{', '.join(unknown)} is not a start time on {day:%Y-%m-%d}; valid times: "
            f"{', '.join(starts) or 'none'}"
        )
    wanted = [starts[time] for time in sorted(set(times))]
    start, end = _day_bounds(day, tz)
    in_day = (blocked_slots_t.c.starts_at >= start, blocked_slots_t.c.starts_at < end)
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


async def _registry(conn: AsyncConnection, slug: str, *, lock: bool = False) -> _Row:
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


# What a merchant's cancellation did (#82): see `core.models.MerchantCancelOutcome`.
CancelOutcome = MerchantCancelOutcome


async def cancel_by_merchant(
    conn: AsyncConnection, slug: str, reservation_id: str, tz: ZoneInfo, now: datetime
) -> tuple[_Row, CancelOutcome]:
    """Cancel a booking and leave the guest a notice, in the same transaction.

    Cancelling an already cancelled booking changes nothing and adds no second notice; a
    booking that has already ended, or that the merchant made itself, is cancelled without one.
    Returns the booking row (with its table label), as it is after the cancellation, and what
    was done.
    """
    wanted_id = reservation_id.strip().upper()
    if not wanted_id.isprintable():
        raise NotFound(f"No booking {reservation_id!r}")
    row = (
        await conn.execute(
            select(reservations_t, tables_t.c.label)
            .join(tables_t, tables_t.c.id == reservations_t.c.table_id)
            .where(reservations_t.c.id == wanted_id)
            .with_for_update(of=reservations_t)
        )
    ).one_or_none()
    if row is None:
        raise NotFound(f"No booking {reservation_id!r}")
    if row.status == "cancelled":
        return row, "guest_cancelled" if row.cancelled_by == "guest" else "already_cancelled"
    await conn.execute(
        update(reservations_t)
        .where(reservations_t.c.id == row.id)
        .values(status="cancelled", cancelled_by="merchant")
    )
    await activity.record_booking(
        conn,
        slug,
        "cancelled",
        "merchant",
        row.id,
        row.guest_name,
        row.party_size,
        row.during.lower,
        at=now,
    )
    # A notice only for a booking a guest made, through an agent or the website (#134): the
    # merchant who entered a booking itself has nobody to tell.
    outcome: CancelOutcome
    if row.during.upper <= now:
        outcome = "ended"
    elif row.via == "merchant":
        outcome = "own_booking"
    else:
        outcome = "notified"
        await _notify_guest(conn, slug, row, tz)
    cancelled = (
        await conn.execute(
            select(reservations_t, tables_t.c.label)
            .join(tables_t, tables_t.c.id == reservations_t.c.table_id)
            .where(reservations_t.c.id == row.id)
        )
    ).one()
    return cancelled, outcome


async def _notify_guest(conn: AsyncConnection, slug: str, row: _Row, tz: ZoneInfo) -> None:
    """Stored notice, shown on the guest's next `get_my_reservations` (AGENTS.md §13)."""
    merchant = (
        await conn.execute(
            select(merchants_t.c.name, merchants_t.c.website).where(merchants_t.c.slug == slug)
        )
    ).one()
    start = row.during.lower.astimezone(tz)
    await conn.execute(
        insert(guest_notices_t).values(
            guest_name=row.guest_name,
            merchant_slug=slug,
            reservation_id=row.id,
            message=(
                f"{merchant.name} cancelled your booking {row.id} on {start:%Y-%m-%d} at "
                f"{start:%H:%M}. Please contact them at {merchant.website} or book again."
            ),
        )
    )


async def notify_move(
    conn: AsyncConnection,
    slug: str,
    reservation_id: str,
    guest_name: str,
    start: datetime,
    tz: ZoneInfo,
) -> None:
    """A guest's booking the merchant moved (#134): told like a cancellation, on the guest's
    next `get_my_reservations`, so that nobody comes at the old time."""
    merchant = (
        await conn.execute(select(merchants_t.c.name).where(merchants_t.c.slug == slug))
    ).one()
    local = start.astimezone(tz)
    await conn.execute(
        insert(guest_notices_t).values(
            guest_name=guest_name,
            merchant_slug=slug,
            reservation_id=reservation_id,
            message=(
                f"{merchant.name} moved your booking {reservation_id} on {local:%Y-%m-%d} to "
                f"{local:%H:%M}."
            ),
        )
    )


def _reasons(error: ValidationError) -> str:
    return "; ".join(str(detail["msg"]) for detail in error.errors())
