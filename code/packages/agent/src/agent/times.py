"""Instants, a booking's range and the merchant's local days: one definition each (#80, #220).

Durations and comparisons of instants are done in UTC; days and times of day are the merchant's
local ones (see `core.availability`).
"""

from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import overload
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.postgresql import Range

from core.errors import InvalidInput


@overload
def utc(moment: datetime) -> datetime: ...
@overload
def utc(moment: None) -> None: ...
def utc(moment: datetime | None) -> datetime | None:
    """The same instant in UTC, so that comparisons hold in the hour that repeats (PEP 495)."""
    return moment.astimezone(UTC) if moment is not None else None


def during(start: datetime, slot_length: timedelta) -> Range[datetime]:
    """The range a booking holds its table: `slot_length` of real time, also across a clock
    change, which wall-clock arithmetic on a `ZoneInfo` datetime would get wrong (#80)."""
    start = start.astimezone(UTC)
    return Range(start, start + slot_length, bounds="[)")


def hhmm(moment: datetime, tz: tzinfo | None = None) -> str:
    """The time of day, `HH:MM`, in `tz`; without it, in `moment`'s own zone (a local time)."""
    local = moment.astimezone(tz) if tz is not None else moment
    return f"{local:%H:%M}"


def day_and_time(moment: datetime, tz: ZoneInfo) -> str:
    """The local day and time, `YYYY-MM-DD HH:MM`, as refusals name a start."""
    return f"{moment.astimezone(tz):%Y-%m-%d %H:%M}"


def parse_hhmm(value: str) -> time:
    """`"19:30"` as a time of day; anything else is `InvalidInput`."""
    try:
        return time.fromisoformat(value)
    except ValueError:
        raise InvalidInput(f"Time must be HH:MM, got {value!r}") from None


def midnight(day: date, tz: ZoneInfo) -> datetime:
    """The start of the merchant's local `day`."""
    return datetime.combine(day, time(), tzinfo=tz)


def day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """The merchant's local day, [00:00, next 00:00): a 23:30 booking stays on its day."""
    try:
        next_day = day + timedelta(days=1)
    except OverflowError:
        raise InvalidInput(f"Date out of range: {day}") from None
    return midnight(day, tz), midnight(next_day, tz)


# How far ahead a prompt looks for the next clock change.
OFFSET_HORIZON_DAYS = 400


def next_offset_change(now: datetime) -> datetime | None:
    """Noon of the first local day, from today, whose UTC offset differs from `now`'s.

    Today counts: before 03:00 on the night of a switch, tonight's services are already in
    the new offset. None when the time zone keeps one offset over the horizon.
    """
    for days in range(OFFSET_HORIZON_DAYS + 1):
        day = datetime.combine(now.date() + timedelta(days=days), time(12), now.tzinfo)
        if day.utcoffset() != now.utcoffset():
            return day
    return None


def clock_line(now: datetime) -> str:
    """Today's date, time and UTC offset, and the next offset change (#83), for a model's prompt:
    the platform assistant's, and the merchants' agents' (#246).

    Tools take times with their offset; a model guessing it would send a summer offset after
    the switch, and `block_slot` would block another hour without failing.
    """
    tz = str(now.tzinfo)
    line = f"Today is {now:%A %Y-%m-%d}, it is {hhmm(now)} ({tz}, UTC{now:%:z})."
    change = next_offset_change(now)
    if change is not None:
        line += (
            f" The offset becomes UTC{change:%:z} on {change:%A %Y-%m-%d}: use it for times"
            " from that date on."
        )
    return line + "\n"
