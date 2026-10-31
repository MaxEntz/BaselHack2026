import ast
import inspect
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from core import availability
from core.availability import (
    BusyInterval,
    TableRow,
    bookable_starts,
    find_slots,
    is_bookable_start,
    pick_table,
)
from core.models import Service

TZ = ZoneInfo("Europe/Zurich")
DAY = date(2026, 10, 31)
DINNER = Service(start="19:00", end="22:00")
LUNCH = Service(start="12:00", end="14:00")
TWO_HOURS = timedelta(minutes=120)
T2 = TableRow(id=1, label="T2", seats=2)
T4 = TableRow(id=2, label="T4", seats=4)
T6 = TableRow(id=3, label="T6", seats=6)
YESTERDAY = datetime(2026, 10, 30, 12, tzinfo=TZ)


def at(hhmm: str, day: date = DAY) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=TZ)


def busy(table: TableRow, start: str, minutes: int = 120) -> BusyInterval:
    return BusyInterval(table.id, at(start), at(start) + timedelta(minutes=minutes))


def slots(
    *,
    tables: tuple[TableRow, ...] = (T2, T4, T6),
    services: tuple[Service, ...] = (DINNER,),
    taken: tuple[BusyInterval, ...] = (),
    blocked: frozenset[datetime] = frozenset(),
    party_size: int = 2,
    requested: str | None = "20:00",
    now: datetime = YESTERDAY,
    slot_length: timedelta = TWO_HOURS,
) -> list[tuple[str, str]]:
    found = find_slots(
        services=services,
        tables=tables,
        busy=taken,
        blocked=blocked,
        day=DAY,
        tz=TZ,
        party_size=party_size,
        requested=time.fromisoformat(requested) if requested else None,
        now=now,
        slot_length=slot_length,
    )
    return [(start.strftime("%H:%M"), table.label) for start, table in found]


# bookable_starts


def test_dinner_starts_every_30_minutes_until_last_seating() -> None:
    assert bookable_starts([DINNER], DAY, TZ) == [
        at("19:00"),
        at("19:30"),
        at("20:00"),
        at("20:30"),
        at("21:00"),
    ]


def test_two_services_give_a_sorted_union() -> None:
    starts = bookable_starts([DINNER, LUNCH], DAY, TZ)

    assert starts == sorted(set(starts))
    assert [start.strftime("%H:%M") for start in starts] == [
        "12:00",
        "12:30",
        "13:00",
        "19:00",
        "19:30",
        "20:00",
        "20:30",
        "21:00",
    ]


def test_shortest_valid_service_has_one_start() -> None:
    shortest = Service(start="19:00", end="20:00")

    assert bookable_starts([shortest], DAY, TZ) == [at("19:00")]


def test_service_shorter_than_last_seating_has_no_starts() -> None:
    # Service validation rejects it; the rule must still hold on its own.
    short = Service.model_construct(start="19:00", end="19:45")

    assert bookable_starts([short], DAY, TZ) == []


@pytest.mark.parametrize(
    ("day", "offset_hours"),
    [
        (date(2026, 3, 28), 1),  # the day before summer time
        (date(2026, 3, 29), 2),  # summer time starts at 02:00
        (date(2026, 10, 24), 2),  # the day before winter time
        (date(2026, 10, 25), 1),  # winter time starts at 03:00
    ],
)
def test_starts_follow_clock_changes(day: date, offset_hours: int) -> None:
    starts = bookable_starts([DINNER], day, TZ)

    assert {start.utcoffset() for start in starts} == {timedelta(hours=offset_hours)}
    assert all(start.date() == day for start in starts)
    assert all(b - a == timedelta(minutes=30) for a, b in pairwise(starts))


def test_start_times_skipped_by_summer_time_are_not_offered() -> None:
    night = Service(start="01:00", end="04:00")

    starts = bookable_starts([night], date(2026, 3, 29), TZ)

    # 02:00 and 02:30 do not exist that night in Europe/Zurich.
    assert [start.strftime("%H:%M") for start in starts] == ["01:00", "01:30", "03:00"]


# is_bookable_start


@pytest.mark.parametrize(
    ("hhmm", "expected"),
    [
        ("19:00", True),
        ("21:00", True),
        ("20:15", False),  # off the 30-minute grid
        ("21:30", False),  # after the last seating
        ("18:30", False),  # before the service
    ],
)
def test_is_bookable_start(hhmm: str, expected: bool) -> None:
    assert is_bookable_start([DINNER], at(hhmm), TZ) is expected


def test_is_bookable_start_compares_instants_not_offsets() -> None:
    assert is_bookable_start([DINNER], at("20:00").astimezone(UTC), TZ)


# pick_table


def test_pick_table_chooses_the_smallest_table_that_fits() -> None:
    assert pick_table([T6, T2, T4], 3, at("20:00"), TWO_HOURS, []) == T4


def test_pick_table_skips_a_table_busy_over_the_slot() -> None:
    assert pick_table([T2, T4, T6], 3, at("20:00"), TWO_HOURS, [busy(T4, "19:00")]) == T6


def test_pick_table_treats_intervals_as_half_open() -> None:
    # 18:00-20:00 ends exactly when 20:00-22:00 starts.
    assert pick_table([T4], 3, at("20:00"), TWO_HOURS, [busy(T4, "18:00")]) == T4
    # 22:00-24:00 starts exactly when 20:00-22:00 ends.
    assert pick_table([T4], 3, at("20:00"), TWO_HOURS, [busy(T4, "22:00")]) == T4


def test_pick_table_returns_none_when_no_table_is_large_enough() -> None:
    assert pick_table([T2, T4, T6], 7, at("20:00"), TWO_HOURS, []) is None


def test_pick_table_breaks_ties_on_label() -> None:
    t1 = TableRow(id=9, label="T1", seats=4)

    assert pick_table([T4, t1], 4, at("20:00"), TWO_HOURS, []) == t1


# find_slots


def test_find_slots_returns_the_three_closest_starts_sorted() -> None:
    assert slots() == [("19:30", "T2"), ("20:00", "T2"), ("20:30", "T2")]


def test_find_slots_skips_blocked_starts_even_with_free_tables() -> None:
    assert slots(blocked=frozenset({at("20:00")})) == [
        ("19:00", "T2"),
        ("19:30", "T2"),
        ("20:30", "T2"),
    ]


def test_find_slots_prefers_the_earlier_start_at_equal_distance() -> None:
    # Only table, 30-minute slots, 20:00 taken: 19:30 and 20:30 are 30 minutes away,
    # 19:00 and 21:00 are 60 minutes away; at equal distance the earlier start wins.
    found = slots(
        tables=(T4,),
        taken=(busy(T4, "20:00", minutes=30),),
        slot_length=timedelta(minutes=30),
    )

    assert found == [("19:00", "T4"), ("19:30", "T4"), ("20:30", "T4")]


def test_find_slots_reports_the_table_that_would_be_assigned() -> None:
    found = slots(taken=(busy(T2, "19:00"),), requested="19:00")

    assert found == [("19:00", "T4"), ("19:30", "T4"), ("20:00", "T4")]


def test_find_slots_never_returns_more_than_the_limit() -> None:
    found = find_slots(
        services=[DINNER, LUNCH],
        tables=[T2, T4, T6],
        busy=[],
        blocked=frozenset(),
        day=DAY,
        tz=TZ,
        party_size=2,
        requested=time(20),
        now=YESTERDAY,
        slot_length=TWO_HOURS,
        limit=2,
    )

    assert [start for start, _ in found] == [at("19:30"), at("20:00")]


def test_find_slots_skips_starts_already_past_today() -> None:
    assert slots(now=at("20:10"), requested=None) == [("20:30", "T2"), ("21:00", "T2")]


def test_find_slots_excludes_a_start_equal_to_now() -> None:
    assert slots(now=at("20:30"), requested=None) == [("21:00", "T2")]


def test_find_slots_returns_nothing_for_a_past_day() -> None:
    assert slots(now=at("09:00", date(2026, 11, 1))) == []


def test_find_slots_without_a_time_returns_the_first_starts_of_the_day() -> None:
    found = slots(services=(DINNER, LUNCH), requested=None)

    assert found == [("12:00", "T2"), ("12:30", "T2"), ("13:00", "T2")]


def test_find_slots_returns_nothing_when_no_table_fits() -> None:
    assert slots(party_size=7) == []


# Purity: AGENTS.md §12 and the issue's "no DB, network or system clock" criterion.


def test_module_has_no_io_or_clock() -> None:
    tree = ast.parse(inspect.getsource(availability))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert imported <= {"bisect", "collections", "core", "dataclasses", "datetime", "zoneinfo"}
    assert not called & {"now", "today", "utcnow", "time"}


# Property: whatever the bookings, an offered slot is really bookable.

HALF_HOURS = [f"{hour:02d}:{minute:02d}" for hour in range(11, 24) for minute in (0, 30)]


@st.composite
def floor_plans(draw: st.DrawFn) -> tuple[TableRow, ...]:
    seats = draw(st.lists(st.integers(1, 8), min_size=1, max_size=5))
    return tuple(TableRow(id=i, label=f"T{i}", seats=n) for i, n in enumerate(seats, 1))


@given(
    tables=floor_plans(),
    data=st.data(),
    party_size=st.integers(1, 8),
    requested=st.none() | st.sampled_from(HALF_HOURS),
    now=st.sampled_from(["11:00", "12:10", "19:45", "21:00"]),
    blocked=st.frozensets(st.sampled_from(HALF_HOURS), max_size=4),
)
def test_offered_slots_never_overlap_a_booking(
    tables: tuple[TableRow, ...],
    data: st.DataObject,
    party_size: int,
    requested: str | None,
    now: str,
    blocked: frozenset[str],
) -> None:
    taken = tuple(
        busy(table, start, minutes)
        for table, start, minutes in data.draw(
            st.lists(
                st.tuples(
                    st.sampled_from(tables),
                    st.sampled_from(HALF_HOURS),
                    st.sampled_from([30, 90, 120]),
                ),
                max_size=10,
            )
        )
    )
    blocked_at = frozenset(at(hhmm) for hhmm in blocked)

    found = find_slots(
        services=[LUNCH, DINNER],
        tables=tables,
        busy=taken,
        blocked=blocked_at,
        day=DAY,
        tz=TZ,
        party_size=party_size,
        requested=time.fromisoformat(requested) if requested else None,
        now=at(now),
        slot_length=TWO_HOURS,
    )

    starts = [start for start, _ in found]
    assert len(found) <= 3
    assert starts == sorted(set(starts))
    for start, table in found:
        assert table.seats >= party_size
        assert start > at(now)
        assert start not in blocked_at
        assert is_bookable_start([LUNCH, DINNER], start, TZ)
        end = start + TWO_HOURS
        assert not any(b.table_id == table.id and b.start < end and start < b.end for b in taken)


# Clock changes (#80): the grid of starts is local time, durations are elapsed time.

SPRING_FORWARD = date(2026, 3, 29)  # 02:00 CET → 03:00 CEST
FALL_BACK = date(2026, 10, 25)  # 03:00 CEST → 02:00 CET
NIGHT = Service(start="00:00", end="04:00")
BEFORE_THE_NIGHTS = datetime(2026, 1, 1, tzinfo=TZ)


def test_a_slot_on_the_spring_forward_night_lasts_two_real_hours() -> None:
    # 01:00 CET is 00:00 UTC: the slot runs to 02:00 UTC, which is 04:00 CEST, past a 03:00
    # CEST booking. Wall-clock arithmetic ended it at "03:00", one real hour only.
    start = datetime.combine(SPRING_FORWARD, time(1), tzinfo=TZ)
    booked = datetime.combine(SPRING_FORWARD, time(3), tzinfo=TZ)

    taken = [BusyInterval(T2.id, booked, booked + TWO_HOURS)]
    assert pick_table([T2], 2, start, TWO_HOURS, taken) is None


def test_a_slot_on_the_fall_back_night_lasts_two_real_hours() -> None:
    # 01:00 CEST is 23:00 UTC: the slot ends at 01:00 UTC (02:00 CET, first pass). A booking
    # from 01:30 UTC doesn't overlap; wall-clock arithmetic ran the slot to 02:00 UTC.
    start = datetime.combine(FALL_BACK, time(1), tzinfo=TZ)
    later = datetime(2026, 10, 25, 1, 30, tzinfo=UTC)

    assert pick_table([T2], 2, start, TWO_HOURS, [BusyInterval(T2.id, later, later)]) == T2


@pytest.mark.parametrize("hhmm", ["02:00", "02:30"])
def test_a_start_in_the_repeated_hour_is_bookable_whatever_its_offset(hhmm: str) -> None:
    # 02:00 and 02:30 CEST on 2026-10-25 are in the hour that repeats. PEP 495 makes `==` false
    # between a ZoneInfo datetime there and any other tzinfo, so compare instants.
    offered = datetime.combine(FALL_BACK, time.fromisoformat(hhmm), tzinfo=TZ)
    from_the_gateway = datetime.fromisoformat(offered.isoformat())

    assert is_bookable_start([NIGHT], from_the_gateway, TZ)
    assert is_bookable_start([NIGHT], offered.astimezone(UTC), TZ)


def test_a_blocked_start_in_the_repeated_hour_is_not_offered() -> None:
    blocked = datetime.fromisoformat("2026-10-25T02:30:00+02:00")

    found = find_slots(
        services=[NIGHT],
        tables=[T2],
        busy=[],
        blocked={blocked},
        day=FALL_BACK,
        tz=TZ,
        party_size=2,
        requested=time(2, 30),
        now=BEFORE_THE_NIGHTS,
        slot_length=TWO_HOURS,
        limit=20,
    )

    offered = {start.astimezone(UTC) for start, _ in found}
    assert offered  # the night has free starts: the test would pass on an empty list
    assert blocked.astimezone(UTC) not in offered


def test_a_start_already_past_in_the_repeated_hour_is_not_offered() -> None:
    # 02:15 CET, second pass of the repeated hour, is after 02:30 CEST in real time.
    now = datetime(2026, 10, 25, 1, 15, tzinfo=UTC).astimezone(TZ)

    found = find_slots(
        services=[NIGHT],
        tables=[T2],
        busy=[],
        blocked=frozenset(),
        day=FALL_BACK,
        tz=TZ,
        party_size=2,
        requested=None,
        now=now,
        slot_length=TWO_HOURS,
        limit=20,
    )

    assert all(start.astimezone(UTC) > now.astimezone(UTC) for start, _ in found)
    assert [f"{start:%H:%M}" for start, _ in found] == ["03:00"]


@st.composite
def night_bookings(
    draw: st.DrawFn, day: date
) -> tuple[tuple[TableRow, ...], tuple[BusyInterval, ...]]:
    """A floor plan and bookings that are real instants, every 30 real minutes from midnight."""
    tables = draw(floor_plans())
    midnight = datetime.combine(day, time(0), tzinfo=TZ).astimezone(UTC)
    taken = tuple(
        BusyInterval(
            table.id,
            midnight + timedelta(minutes=30 * step),
            midnight + timedelta(minutes=30 * step + minutes),
        )
        for table, step, minutes in draw(
            st.lists(
                st.tuples(
                    st.sampled_from(tables), st.integers(0, 10), st.sampled_from([30, 90, 120])
                ),
                max_size=10,
            )
        )
    )
    return tables, taken


NIGHTS = st.sampled_from([SPRING_FORWARD, FALL_BACK]).flatmap(
    lambda day: st.tuples(st.just(day), night_bookings(day))
)


# Without bookings, each night's whole grid: the repeated hour comes back with a fixed offset.
@example(night=(SPRING_FORWARD, ((T2,), ())), party_size=2, requested=None)
@example(night=(FALL_BACK, ((T2,), ())), party_size=2, requested="02:30")
@given(
    night=NIGHTS,
    party_size=st.integers(1, 8),
    requested=st.none() | st.sampled_from(["00:30", "01:30", "02:00", "02:30", "03:00"]),
)
def test_offered_slots_never_overlap_a_booking_on_clock_change_nights(
    night: tuple[date, tuple[tuple[TableRow, ...], tuple[BusyInterval, ...]]],
    party_size: int,
    requested: str | None,
) -> None:
    day, (tables, taken) = night

    found = find_slots(
        services=[NIGHT],
        tables=tables,
        busy=taken,
        blocked=frozenset(),
        day=day,
        tz=TZ,
        party_size=party_size,
        requested=time.fromisoformat(requested) if requested else None,
        now=BEFORE_THE_NIGHTS,
        slot_length=TWO_HOURS,
        limit=20,
    )

    for start, table in found:
        # Booked through the gateway, the same start comes back with a fixed offset.
        assert is_bookable_start([NIGHT], datetime.fromisoformat(start.isoformat()), TZ)
        real_start = start.astimezone(UTC)
        end = real_start + TWO_HOURS
        assert not any(
            b.table_id == table.id and b.start < end and real_start < b.end for b in taken
        )
