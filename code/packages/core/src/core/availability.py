"""Pure availability rules: slot generation, table choice, blocked slots. No I/O.

AGENTS.md §6. Times are aware datetimes in the merchant's timezone; `now` is always passed in,
so the rules never read the clock. The grid of starts is local time; durations are elapsed
time, computed in UTC: Python adds a `timedelta` to a `ZoneInfo` datetime in wall-clock time,
which makes a slot 1 or 3 real hours on a clock-change night (#80).
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from core.models import MIN_SERVICE_MINUTES, Service

STEP_MIN = 30
# Same rule as service validation: the shortest valid service has exactly one start.
LAST_SEATING_OFFSET_MIN = MIN_SERVICE_MINUTES


@dataclass(frozen=True)
class BusyInterval:
    """A confirmed reservation holding a table over the half-open range [start, end)."""

    table_id: int
    start: datetime
    end: datetime


@dataclass(frozen=True)
class TableRow:
    id: int
    label: str
    seats: int


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _local(day: date, minute: int, tz: ZoneInfo) -> datetime | None:
    """`day` at `minute` past midnight in `tz`, or None when a clock change skips that time."""
    wall = datetime.combine(day, time(minute // 60, minute % 60), tzinfo=tz)
    # A skipped time (02:30 when summer time starts) does not survive a round trip.
    if wall.astimezone(UTC).astimezone(tz).replace(tzinfo=None) != wall.replace(tzinfo=None):
        return None
    return wall


def bookable_starts(
    services: Sequence[Service],
    day: date,
    tz: ZoneInfo,
    step_min: int = STEP_MIN,
    last_seating_offset_min: int = LAST_SEATING_OFFSET_MIN,
) -> list[datetime]:
    """Start times on `day`: every `step_min` from each service's start to its last seating."""
    starts: set[datetime] = set()
    for service in services:
        last = _minutes(service.end) - last_seating_offset_min
        for minute in range(_minutes(service.start), last + 1, step_min):
            start = _local(day, minute, tz)
            if start is not None:
                starts.add(start)
    return sorted(starts)


def is_bookable_start(services: Sequence[Service], start: datetime, tz: ZoneInfo) -> bool:
    """True when `start` is on the grid of one of the services, on its own local day."""
    return start in bookable_starts(services, start.astimezone(tz).date(), tz)


def pick_table(
    tables: Sequence[TableRow],
    party_size: int,
    start: datetime,
    slot_length: timedelta,
    busy: Sequence[BusyInterval],
) -> TableRow | None:
    """The smallest free table that seats the party, ties broken by label."""
    start = start.astimezone(UTC)
    end = start + slot_length
    taken = {b.table_id for b in busy if b.start < end and start < b.end}
    fitting = (t for t in tables if t.seats >= party_size and t.id not in taken)
    return min(fitting, key=lambda t: (t.seats, t.label), default=None)


def find_slots(
    *,
    services: Sequence[Service],
    tables: Sequence[TableRow],
    busy: Sequence[BusyInterval],
    blocked: Collection[datetime],
    day: date,
    tz: ZoneInfo,
    party_size: int,
    requested: time | None,
    now: datetime,
    slot_length: timedelta,
    limit: int = 3,
) -> list[tuple[datetime, TableRow]]:
    """Up to `limit` free starts on `day`, each with the table it would get, sorted by time.

    With `requested`, the starts closest to it win, the earlier one at equal distance;
    without it, the first starts of the day. Blocked and past starts (`<= now`) are skipped.
    """
    free: list[tuple[datetime, TableRow]] = []
    for start in bookable_starts(services, day, tz):
        if start <= now or start in blocked:
            continue
        table = pick_table(tables, party_size, start, slot_length, busy)
        if table is not None:
            free.append((start, table))

    if requested is not None:
        target = datetime.combine(day, requested, tzinfo=tz)
        free.sort(key=lambda slot: (abs(slot[0] - target), slot[0]))
    return sorted(free[:limit], key=lambda slot: slot[0])
