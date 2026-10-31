from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from agent.service import AgentService, PgAgentService, candidate_tables, check_start, same_guest
from core.availability import TableRow
from core.db import make_engine
from core.errors import SlotUnavailable
from core.models import Service

TZ = ZoneInfo("Europe/Zurich")
DAY = date(2026, 11, 2)
DINNER = [Service(start="19:00", end="22:00")]
T1 = TableRow(id=1, label="T1", seats=4)
T2 = TableRow(id=2, label="T2", seats=4)
T6 = TableRow(id=3, label="T6", seats=6)
SMALL = TableRow(id=4, label="T0", seats=2)


def at(hhmm: str) -> datetime:
    return datetime.combine(DAY, time.fromisoformat(hhmm), tzinfo=TZ)


@pytest.mark.parametrize(
    ("given", "booked", "expected"),
    [
        ("Sam Dupont", "Sam Dupont", True),
        ("  sam   DUPONT ", "Sam Dupont", True),
        ("Sam\tDupont", "Sam Dupont", True),
        ("anna weiss", "Anna Weiß", True),
        ("Anna Weber", "Anna\u00a0Weber", True),
        ("Sam", "Sam Dupont", False),
        ("Sam Dupond", "Sam Dupont", False),
    ],
)
def test_same_guest_ignores_case_and_spacing(given: str, booked: str, expected: bool) -> None:
    assert same_guest(given, booked) is expected


def test_candidates_are_tables_that_fit_by_size_then_label() -> None:
    assert candidate_tables([T6, T2, SMALL, T1], 3) == [T1, T2, T6]


def test_candidates_put_the_current_table_first_when_it_fits() -> None:
    assert candidate_tables([T6, T2, SMALL, T1], 3, current_id=T6.id) == [T6, T1, T2]
    assert candidate_tables([T6, T2, SMALL, T1], 3, current_id=SMALL.id) == [T1, T2, T6]


def test_check_start_accepts_a_future_start_on_the_grid() -> None:
    check_start(DINNER, at("20:00"), now=at("09:00"), tz=TZ)


@pytest.mark.parametrize(
    ("hhmm", "now", "match"),
    [
        ("20:15", "09:00", "not a start time"),
        ("21:30", "09:00", "not a start time"),
        ("20:00", "20:00", "past"),
        ("20:00", "20:30", "past"),
    ],
)
def test_check_start_rejects(hhmm: str, now: str, match: str) -> None:
    with pytest.raises(SlotUnavailable, match=match):
        check_start(DINNER, at(hhmm), now=at(now), tz=TZ)


def test_check_start_lists_the_valid_times() -> None:
    with pytest.raises(SlotUnavailable, match="19:00, 19:30, 20:00, 20:30, 21:00"):
        check_start(DINNER, at("20:15"), now=at("09:00"), tz=TZ)


def test_pg_service_implements_the_contract() -> None:
    engine = make_engine("postgresql+psycopg://u:p@localhost:5432/db")
    service: AgentService = PgAgentService(engine, tz=TZ)

    assert isinstance(service, PgAgentService)


def test_check_start_compares_instants_in_the_repeated_hour() -> None:
    # 02:30 CEST on 2026-10-25 is before 02:15 CET (second pass) in real time; two ZoneInfo
    # datetimes compare by wall clock, so compare instants (#80).
    night = [Service(start="00:00", end="04:00")]
    start = datetime(2026, 10, 25, 2, 30, tzinfo=TZ)
    now = datetime(2026, 10, 25, 1, 15, tzinfo=UTC).astimezone(TZ)

    with pytest.raises(SlotUnavailable, match="in the past"):
        check_start(night, start, now=now, tz=TZ)
