"""La Graine Verte's management data (#131): the mockup's fixture, in the database.

`tests/fixtures/demo/la-graine-verte-management.json` comes from the mockup
(`scripts/export_management_fixture.py`), and the web mock reads it too. Its 7 days of bookings
go in as they are, with their references: they take R-001 to R-055 from the merchant's sequence,
so the next booking, the demo's live one, is R-056. The 29 days before today only have totals
there, so `history_bookings` makes bookings that add up to them exactly. The agent requests go to
`public.agent_requests`, as the BATP node logs them (#146). Every date moves so that the
fixture's today is the seed's, local times kept, inside the frames too. `create_reservation`
refuses past times, so these are Core inserts; the exclusion constraint still applies.
"""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import ceil
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from agent.times import during
from core.availability import BusyInterval, TableRow, bookable_starts, pick_table
from core.db import (
    agent_requests_t,
    blocked_slots_t,
    merchant_activity_t,
    merchant_conn,
    merchants_t,
    next_reservation_id,
    policy_t,
    reservations_t,
    tables_t,
)
from core.errors import InvalidInput, NotFound
from core.models import Service, Via

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "demo"
    / "la-graine-verte-management.json"
)
# The assistant's opening conversation in the mockup, as the web chat's items.
CONVERSATION = FIXTURE.with_name("la-graine-verte-assistant.json")
MERCHANT = "la-graine-verte"
KEY = "management-"  # idempotency keys of the seeded bookings: `management-R-001`, …
# Names for the past days' guests (the mockup's lists).
FIRST = ("Alex", "Sam", "Nora", "Lou", "Noa", "Eli", "Claire", "Yann", "Ines", "Jo", "Mia", "Tom")
LAST = ("Martin", "Dupont", "Haas", "Fischer", "Keller", "Brun", "Roth", "Meyer", "Vogt", "Bauer")
# A day, or an instant with its offset, as the fixture writes them (frames included).
STAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z))?\b")


def guest_name(n: int) -> str:
    return f"{FIRST[n % len(FIRST)]} {LAST[n * 7 % len(LAST)]}"


def load() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text())
    return data


def moved(text: str, shift: timedelta, tz: ZoneInfo) -> str:
    """`text` with every day and instant `shift` later, instants at the same local time."""

    def move(match: re.Match[str]) -> str:
        if "T" not in match[0]:
            return (date.fromisoformat(match[0]) + shift).isoformat()
        local = datetime.fromisoformat(match[0]).astimezone(tz)
        return datetime.combine(local.date() + shift, local.time(), tz).isoformat()

    return STAMP.sub(move, text)


@dataclass(frozen=True)
class Shift:
    """How the fixture's dates move to the seed's today, local times kept (`moved`)."""

    days: timedelta
    tz: ZoneInfo

    def at(self, stamp: str) -> datetime:
        return datetime.fromisoformat(moved(stamp, self.days, self.tz))

    def day(self, value: str) -> date:
        return date.fromisoformat(value) + self.days

    def text(self, value: str) -> str:
        return moved(value, self.days, self.tz)


@dataclass(frozen=True)
class Floor:
    """Where and when the merchant's bookings go: its tables, services and slot length."""

    tables: list[TableRow]
    services: list[Service]
    slot_length: timedelta


def parties(count: int, guests: int) -> list[int]:
    """`count` party sizes adding up to `guests`, as even as possible, larger ones first."""
    if count == 0:
        return []
    base, extra = divmod(guests, count)
    return [base + 1] * extra + [base] * (count - extra)


def split_day(
    bookings: int, guests: int, agent_guests: int, largest: int = 4
) -> list[tuple[int, Via]]:
    """Party sizes and channels of a past day's live bookings, adding up to its totals.

    Agent bookings take their share of the guests, within what parties of 1 to `largest` allow.
    """
    others = guests - agent_guests
    low = max(ceil(agent_guests / largest), bookings - others)
    high = min(agent_guests, bookings - ceil(others / largest))
    if not (
        bookings <= guests <= largest * bookings and 0 <= agent_guests <= guests and low <= high
    ):
        raise ValueError(
            f"no {bookings} bookings make {guests} guests, {agent_guests} via an agent"
        )
    agent = min(max(round(bookings * agent_guests / guests), low), high)
    return [(size, "claude") for size in parties(agent, agent_guests)] + [
        (size, "merchant") for size in parties(bookings - agent, others)
    ]


def packing_order(
    services: list[Service], day: date, tz: ZoneInfo, slot_length: timedelta
) -> list[datetime]:
    """The day's starts, those one slot apart from a service's opening first: a table then holds
    two bookings per service."""
    opening = {s.start for s in services}

    def key(start: datetime) -> tuple[bool, datetime]:
        local = start.astimezone(tz)
        aligned = any(
            (local - datetime.combine(day, time.fromisoformat(o), tz)) % slot_length == timedelta()
            and local.strftime("%H:%M") >= o
            for o in opening
        )
        return not aligned, start

    return sorted(bookable_starts(services, day, tz), key=key)


def history_bookings(
    history: list[dict[str, Any]],
    tables: list[TableRow],
    services: list[Service],
    tz: ZoneInfo,
    slot_length: timedelta,
) -> list[dict[str, Any]]:
    """Rows for `reservations_t` making each day's totals: its live bookings, then its
    cancelled ones. References are `H-<MMDD>-<n>`, outside the merchant's `R-` sequence."""
    largest = max(t.seats for t in tables)
    rows: list[dict[str, Any]] = []
    for entry in history:
        day = date.fromisoformat(entry["date"])
        order = packing_order(services, day, tz, slot_length)
        split = split_day(entry["bookings"], entry["guests"], entry["agent_guests"], largest)
        busy: list[BusyInterval] = []
        placed: list[tuple[datetime, TableRow, int, Via, bool]] = []
        for size, via in split:
            spot = next(
                (
                    (start, table)
                    for start in order
                    if (table := pick_table(tables, size, start, slot_length, busy))
                ),
                None,
            )
            if spot is None:
                raise ValueError(f"{day}: no table left for a party of {size}")
            start, table = spot
            busy.append(BusyInterval(table.id, start, start + slot_length))
            placed.append((start, table, size, via, False))
        for n in range(entry["cancelled"]):  # cancelled bookings hold no table
            cancelled_via: Via = "claude" if n % 2 == 0 else "merchant"
            table = min((t for t in tables if t.seats >= 2), key=lambda t: (t.seats, t.label))
            placed.append((order[n % len(order)], table, 2, cancelled_via, True))
        for n, (start, table, size, via, cancelled) in enumerate(placed, start=1):
            reference = f"H-{day:%m%d}-{n:02d}"
            rows.append(
                {
                    "id": reference,
                    "table_id": table.id,
                    "guest_name": guest_name(len(rows)),
                    "party_size": size,
                    "during": during(start, slot_length),
                    "via": via,
                    "status": "cancelled" if cancelled else "confirmed",
                    "cancelled_by": ("guest" if via == "claude" else "merchant")
                    if cancelled
                    else None,
                    "idempotency_key": KEY + reference,
                    "created_at": start - timedelta(days=2),
                }
            )
    return rows


def activity_lines(
    feed: list[dict[str, Any]],
    bookings: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
    shift: Shift,
) -> list[dict[str, Any]]:
    """Home's feed (#135): the fixture's lines, oldest first, with the details the server words
    them from (the booking's guest, party and start; the blocked times) instead of their text."""
    by_id = {b["id"]: b for b in bookings}
    lines = []
    for item in reversed(feed):
        details: dict[str, Any] = {}
        booking = by_id.get(item["reservation_id"] or "")
        if booking is not None:
            details = {
                "guest": booking["guest_name"],
                "party": booking["party_size"],
                # Local, as a booking's own line records it (`agent.activity.record_booking`).
                "starts_at": booking["during"].lower.astimezone(shift.tz).isoformat(),
            }
        elif item["kind"] in ("blocked", "reopened"):
            details = {"times": [b["starts_at"].isoformat() for b in blocked]}
        lines.append(
            {
                "merchant_slug": MERCHANT,
                "at": shift.at(item["at"]),
                "kind": item["kind"],
                "source": item["source"],
                "reservation_id": item["reservation_id"],
                "details": details,
            }
        )
    return lines


async def seed_management(engine: AsyncEngine, today: date) -> str:
    """Load the fixture into La Graine Verte, moved to `today`; a second run changes nothing."""
    data = load()
    tz = ZoneInfo(data["merchant"]["timezone"])
    shift = Shift(today - datetime.fromisoformat(data["now"]).date(), tz)
    await _check_registered(engine)
    async with merchant_conn(engine, MERCHANT) as conn:
        if await _already_seeded(conn, data):
            return "management data kept"
        floor = await _floor(conn)
        rows = await _week_rows(conn, data["reservations"], floor, shift)
        past = [
            {**entry, "date": shift.day(entry["date"]).isoformat()}
            for entry in data["history"][:-1]
        ]
        rows += history_bookings(past, floor.tables, floor.services, tz, floor.slot_length)
        blocked = [{"starts_at": shift.at(b["starts_at"])} for b in data["blocked"]]
        requests = _agent_requests(data["agent_requests"], shift)
        lines = activity_lines(data["activity"], rows, blocked, shift)
        for table, values in (
            (reservations_t, rows),
            (blocked_slots_t, blocked),
            (agent_requests_t, requests),
            (merchant_activity_t, lines),
        ):
            if values:
                await conn.execute(insert(table), values)
    week = len(data["reservations"])
    return (
        f"management data: {week} bookings over the 7 days from {today}, "
        f"{len(rows) - week} over the 29 days before, {len(blocked)} blocked time(s), "
        f"{len(requests)} agent requests"
    )


async def _check_registered(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        if not await conn.scalar(select(merchants_t.c.slug).where(merchants_t.c.slug == MERCHANT)):
            raise NotFound(f"{MERCHANT} isn't in the registry: seed it too (`--all`)")


async def _already_seeded(conn: AsyncConnection, data: dict[str, Any]) -> bool:
    """True when the fixture is already in; refused when other bookings would clash with it."""
    first = KEY + data["reservations"][0]["id"]
    if await conn.scalar(
        select(reservations_t.c.id).where(reservations_t.c.idempotency_key == first)
    ):
        return True
    if await conn.scalar(select(func.count()).select_from(reservations_t)):
        raise InvalidInput(
            f"{MERCHANT} already has bookings, so the fixture's references would clash: "
            "`make demo-reset` first"
        )
    return False


async def _floor(conn: AsyncConnection) -> Floor:
    tables = [
        TableRow(row.id, row.label, row.seats)
        for row in await conn.execute(select(tables_t.c.id, tables_t.c.label, tables_t.c.seats))
    ]
    policy = (await conn.execute(select(policy_t.c.slot_length_min, policy_t.c.services))).one()
    return Floor(
        tables=tables,
        services=[Service.model_validate(s) for s in policy.services],
        slot_length=timedelta(minutes=policy.slot_length_min),
    )


async def _week_rows(
    conn: AsyncConnection, bookings: list[dict[str, Any]], floor: Floor, shift: Shift
) -> list[dict[str, Any]]:
    """Rows for `reservations_t` of the fixture's 7 days, under the fixture's references."""
    by_label = {t.label: t for t in floor.tables}
    rows = []
    for booking in bookings:
        # The sequence hands out the fixture's references, so it goes on from there.
        if await next_reservation_id(conn) != booking["id"]:
            raise InvalidInput(f"{MERCHANT}'s booking references don't start at R-001")
        day = shift.day(booking["date"])
        start = datetime.combine(day, time.fromisoformat(booking["time"]), shift.tz)
        rows.append(
            {
                "id": booking["id"],
                "table_id": by_label[booking["table_label"]].id,
                "guest_name": booking["guest_name"],
                "party_size": booking["party_size"],
                "during": during(start, floor.slot_length),
                "via": booking["via"],
                "status": booking["status"],
                "cancelled_by": booking["cancelled_by"],
                "idempotency_key": KEY + booking["id"],
                "created_at": shift.at(booking["created_at"]),
            }
        )
    return rows


def _agent_requests(requests: list[dict[str, Any]], shift: Shift) -> list[dict[str, Any]]:
    """Rows for `agent_requests_t`, oldest first so that ids follow time, frames moved too."""
    return [
        {
            "merchant_slug": MERCHANT,
            "at": shift.at(r["at"]),
            "agent_name": r["agent_name"],
            "intent": r["intent"],
            "status": r["status"],
            "request": shift.text(r["request"]),
            "response": shift.text(r["response"]),
        }
        for r in reversed(requests)
    ]
