"""Times of day, instants and the merchant's local days: one helper each (#220)."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from agent import times
from core.errors import InvalidInput
from core.time_of_day import from_minutes, to_minutes

TZ = ZoneInfo("Europe/Zurich")


@pytest.mark.parametrize(
    ("hhmm", "minutes"), [("00:00", 0), ("09:05", 545), ("19:30", 1170), ("23:59", 1439)]
)
def test_minutes_and_their_inverse(hhmm: str, minutes: int) -> None:
    assert to_minutes(hhmm) == minutes
    assert from_minutes(minutes) == time.fromisoformat(hhmm)


def test_utc_keeps_the_instant_and_none() -> None:
    local = datetime(2026, 11, 2, 20, 0, tzinfo=TZ)

    assert times.utc(local) == local
    assert times.utc(local).tzinfo is UTC
    assert times.utc(None) is None


def test_a_booking_holds_its_table_for_real_time_across_a_clock_change() -> None:
    # 01:30 CEST on 2026-10-25 (23:30 UTC the day before), the night summer time ends (#80).
    # In wall-clock time, 2 hours later would be 03:30 CET: 3 real hours. Subtracting two
    # datetimes of one `ZoneInfo` compares their wall clocks too, so compare in UTC.
    start = datetime(2026, 10, 25, 1, 30, tzinfo=TZ)
    span = times.during(start, timedelta(minutes=120))

    assert span.lower == start
    assert span.upper == datetime(2026, 10, 25, 1, 30, tzinfo=UTC)  # 02:30 CET
    assert times.utc(span.upper) - times.utc(span.lower) == timedelta(minutes=120)
    assert span.bounds == "[)"


def test_local_wording_of_an_instant() -> None:
    moment = datetime(2026, 11, 2, 19, 30, tzinfo=UTC)

    assert times.hhmm(moment, TZ) == "20:30"
    assert times.day_and_time(moment, TZ) == "2026-11-02 20:30"


def test_parse_hhmm() -> None:
    assert times.parse_hhmm("20:30") == time(20, 30)
    with pytest.raises(InvalidInput, match="Time must be HH:MM, got 'soon'"):
        times.parse_hhmm("soon")


def test_a_local_day_runs_midnight_to_midnight() -> None:
    start, end = times.day_bounds(date(2026, 11, 2), TZ)

    assert start == times.midnight(date(2026, 11, 2), TZ)
    assert (start.tzinfo, start.hour, end.date(), end.hour) == (TZ, 0, date(2026, 11, 3), 0)


def test_the_night_summer_time_ends_lasts_25_hours() -> None:
    start, end = times.day_bounds(date(2026, 10, 25), TZ)

    assert times.utc(end) - times.utc(start) == timedelta(hours=25)


def test_the_last_day_has_no_bounds() -> None:
    with pytest.raises(InvalidInput, match="Date out of range"):
        times.day_bounds(date.max, TZ)
