"""Instants, a booking's range and the merchant's local days: one definition each (#80, #220).

Durations and comparisons of instants are done in UTC; days and times of day are the merchant's
local ones (see `core.availability`).
"""

from datetime import UTC, date, datetime, time, timedelta
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


def hhmm(moment: datetime, tz: ZoneInfo) -> str:
    """The local time of day, `HH:MM`."""
    return f"{moment.astimezone(tz):%H:%M}"


def day_and_time(moment: datetime, tz: ZoneInfo) -> str:
    """The local day and time, `YYYY-MM-DD HH:MM`, as refusals name a start."""
    return f"{moment.astimezone(tz):%Y-%m-%d %H:%M}"


def parse_hhmm(value: str) -> time:
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
