"""The candidate-table loop every booking write shares (#8, #134, #220), and the rules around it.

`attempt` (one SAVEPOINT on one table) needs Postgres: the integration tests cover it. Here it
is replaced by a script of outcomes, to pin the loop's own decisions.
"""

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Executable, select

from agent import times, writes
from agent.merchant import Merchant
from core.availability import TableRow
from core.db import tables_t
from core.errors import BookingStarted, PartyTooLarge
from core.models import Service

TZ = ZoneInfo("Europe/Zurich")
T1 = TableRow(id=1, label="T1", seats=2)
T2 = TableRow(id=2, label="T2", seats=4)
T3 = TableRow(id=3, label="T3", seats=4)


class Script:
    """Answers `attempt` with the next outcome, and records the tables it was asked for."""

    def __init__(self, outcomes: list[writes.Outcome]) -> None:
        self.outcomes: Iterator[writes.Outcome] = iter(outcomes)
        self.tables: list[int] = []
        self.statements: list[Executable] = []

    async def __call__(
        self, conn: object, table_id: int, party_size: int, statement: Executable
    ) -> writes.Outcome:
        self.tables.append(table_id)
        self.statements.append(statement)
        return next(self.outcomes)


def write(table: TableRow) -> Executable:
    return select(tables_t.c.id).where(tables_t.c.id == table.id)


Install = Callable[[list[writes.Outcome]], Script]


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch) -> Install:
    def install(outcomes: list[writes.Outcome]) -> Script:
        fake = Script(outcomes)
        monkeypatch.setattr(writes, "attempt", fake)
        return fake

    return install


async def test_the_first_table_that_takes_the_write_is_the_booking_s(script: Install) -> None:
    fake = script(["taken", "done"])

    table = await writes.first_free(None, [T1, T2, T3], 2, write)  # type: ignore[arg-type]

    assert table == T2
    assert fake.tables == [T1.id, T2.id]  # T3 never tried
    assert len(fake.statements) == 2  # one statement per table, built for it


async def test_no_table_left_is_none(script: Install) -> None:
    fake = script(["taken", "taken", "taken"])

    assert await writes.first_free(None, [T1, T2, T3], 2, write) is None  # type: ignore[arg-type]
    assert fake.tables == [T1.id, T2.id, T3.id]


async def test_a_concurrent_request_with_the_same_key_ends_the_search(script: Install) -> None:
    # Its booking is the caller's answer: no other table is tried.
    fake = script(["key_taken"])

    assert await writes.first_free(None, [T1, T2, T3], 2, write) is None  # type: ignore[arg-type]
    assert fake.tables == [T1.id]


async def test_no_candidate_tries_nothing(script: Install) -> None:
    fake = script([])

    assert await writes.first_free(None, [], 2, write) is None  # type: ignore[arg-type]
    assert fake.tables == []


def test_a_booking_changes_only_before_it_starts() -> None:
    start = datetime(2026, 11, 2, 20, 0, tzinfo=TZ)

    writes.check_not_started("R-001", start, now=start - timedelta(minutes=1))
    with pytest.raises(BookingStarted, match="Booking R-001 has already started"):
        writes.check_not_started("R-001", start, now=start)


def merchant(max_party_size: int, *seats: int) -> Merchant:
    return Merchant(
        name="Le Petit Zinc",
        max_party_size=max_party_size,
        slot_length=timedelta(minutes=120),
        services=(Service(start="19:00", end="22:00"),),
        tables=tuple(TableRow(id=n, label=f"T{n}", seats=s) for n, s in enumerate(seats, 1)),
    )


@pytest.mark.parametrize(
    ("max_party_size", "seats", "largest"), [(6, (2, 4), 4), (4, (2, 6), 4), (8, (), 0)]
)
def test_the_largest_group_is_the_limit_or_the_largest_table(
    max_party_size: int, seats: tuple[int, ...], largest: int
) -> None:
    assert merchant(max_party_size, *seats).largest_group == largest


@pytest.mark.parametrize(("max_party_size", "seats"), [(6, (2, 4)), (4, (2, 6))])
def test_a_party_above_the_largest_group_gets_the_fallback(
    max_party_size: int, seats: tuple[int, ...]
) -> None:
    plan = merchant(max_party_size, *seats)
    plan.check_party(4)

    with pytest.raises(
        PartyTooLarge, match=r"^Le Petit Zinc takes online bookings for up to 4 guests$"
    ):
        plan.check_party(5)


def test_a_move_writes_its_table_and_range_and_its_party_only_when_given() -> None:
    span = times.during(datetime(2026, 11, 2, 20, 0, tzinfo=TZ), timedelta(minutes=120))

    guest = writes.moved_to("R-001", span, party_size=3)(T2).compile().params
    merchant = writes.moved_to("R-001", span)(T2).compile().params

    assert (guest["table_id"], guest["during"], guest["party_size"]) == (T2.id, span, 3)
    assert (merchant["table_id"], merchant["during"]) == (T2.id, span)
    assert "party_size" not in merchant
    assert "R-001" in guest.values()
    assert "R-001" in merchant.values()
