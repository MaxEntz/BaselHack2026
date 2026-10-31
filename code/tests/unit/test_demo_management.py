"""La Graine Verte's management data (#131): the mockup's numbers, and the bookings the seed
makes for the days the mockup only gives totals for."""

import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from itertools import combinations
from typing import Any
from zoneinfo import ZoneInfo

import demo_management
import pytest

from core.availability import TableRow, bookable_starts
from core.models import LiveEvent, Service

DATA = demo_management.load()
TODAY = date.fromisoformat(DATA["now"][:10])
TZ = ZoneInfo(DATA["merchant"]["timezone"])
SLOT = timedelta(minutes=DATA["rules"]["slot_length_min"])
TABLES = [TableRow(n, t["label"], t["seats"]) for n, t in enumerate(DATA["tables"], start=1)]
SERVICES = [Service.model_validate(s) for s in DATA["hours"]["services"]]
PAST = DATA["history"][:-1]


def dashboard(bookings: list[dict[str, Any]]) -> tuple[int, int, int, int, int, int]:
    """Home's numbers: 30 days of guests, through the agent, cancelled; tonight's guests and
    bookings; guests over the 7 days from today."""
    live = [b for b in bookings if b["status"] == "confirmed"]
    days = [*PAST, today_totals(bookings)]
    tonight = [
        b for b in live if b["date"] == TODAY.isoformat() and b["time"] >= SERVICES[-1].start
    ]
    return (
        sum(d["guests"] for d in days),
        sum(d["agent_guests"] for d in days),
        sum(d["cancelled"] for d in days),
        sum(b["party_size"] for b in tonight),
        len(tonight),
        sum(b["party_size"] for b in live),
    )


def today_totals(bookings: list[dict[str, Any]]) -> dict[str, int]:
    today = [b for b in bookings if b["date"] == TODAY.isoformat()]
    live = [b for b in today if b["status"] == "confirmed"]
    return {
        "guests": sum(b["party_size"] for b in live),
        "agent_guests": sum(b["party_size"] for b in live if b["via"] == "claude"),
        "cancelled": sum(1 for b in today if b["status"] == "cancelled"),
    }


def test_home_shows_the_mockups_numbers_before_and_after_the_live_booking() -> None:
    event = DATA["live_booking"]
    kim = {**event["reservation"], "date": event["date"]}

    assert dashboard(DATA["reservations"]) == (982, 597, 42, 17, 6, 146)
    assert dashboard([*DATA["reservations"], kim]) == (984, 599, 42, 19, 7, 148)


@pytest.mark.parametrize("day", PAST, ids=lambda day: day["date"])
def test_a_past_day_splits_into_its_totals(day: dict[str, Any]) -> None:
    split = demo_management.split_day(day["bookings"], day["guests"], day["agent_guests"])

    assert len(split) == day["bookings"]
    assert sum(size for size, _ in split) == day["guests"]
    assert sum(size for size, via in split if via == "claude") == day["agent_guests"]
    assert all(1 <= size <= 4 for size, _ in split)
    assert split == demo_management.split_day(day["bookings"], day["guests"], day["agent_guests"])


@pytest.mark.parametrize(
    ("bookings", "guests", "agent_guests"),
    [(2, 9, 0), (3, 2, 0), (1, 2, 1), (2, 4, 5)],
)
def test_a_day_no_bookings_can_make_is_refused(
    bookings: int, guests: int, agent_guests: int
) -> None:
    with pytest.raises(ValueError, match=r"^no \d+ bookings make"):
        demo_management.split_day(bookings, guests, agent_guests)


def test_past_bookings_make_each_days_totals_without_sharing_a_table() -> None:
    rows = demo_management.history_bookings(PAST, TABLES, SERVICES, TZ, SLOT)
    by_day: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_day[row["during"].lower.astimezone(TZ).date()].append(row)

    assert len({row["id"] for row in rows}) == len(rows)
    assert all(row["id"].startswith("H-") for row in rows)  # outside the R- sequence
    for day in PAST:
        booked = by_day[date.fromisoformat(day["date"])]
        live = [row for row in booked if row["status"] == "confirmed"]
        starts = set(bookable_starts(SERVICES, date.fromisoformat(day["date"]), TZ))
        assert len(live) == day["bookings"], day["date"]
        assert sum(row["party_size"] for row in live) == day["guests"]
        assert (
            sum(row["party_size"] for row in live if row["via"] == "claude") == day["agent_guests"]
        )
        assert len(booked) - len(live) == day["cancelled"]
        assert all(row["during"].lower in starts for row in booked)
        for a, b in combinations(live, 2):
            if a["table_id"] == b["table_id"]:
                assert (
                    a["during"].upper <= b["during"].lower or b["during"].upper <= a["during"].lower
                ), (a["id"], b["id"])
    assert rows == demo_management.history_bookings(PAST, TABLES, SERVICES, TZ, SLOT)


def test_channels_count_the_past_bookings_and_todays() -> None:
    rows = demo_management.history_bookings(PAST, TABLES, SERVICES, TZ, SLOT)
    channels = Counter(row["via"] for row in rows if row["status"] == "confirmed")
    channels.update(
        b["via"]
        for b in DATA["reservations"]
        if b["date"] == TODAY.isoformat() and b["status"] == "confirmed"
    )

    assert {c["via"]: c["bookings"] for c in DATA["channels"]} == channels


def test_the_opening_conversation_tells_the_fixtures_story() -> None:
    opening = json.loads(demo_management.CONVERSATION.read_text())["opening"]
    ping, *turns = opening
    event = LiveEvent.model_validate(ping["event"])
    booked = {b["id"]: b for b in DATA["reservations"]}[event.reservation.id]
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    sunday = [b for b in DATA["reservations"] if b["date"] == tomorrow]
    live = [b for b in sunday if b["status"] == "confirmed"]
    kept = [b["guest_name"] for b in live if b["time"] == "13:00"]

    assert ping["type"] == "notification"
    assert (event.date.isoformat(), event.reservation.time) == (booked["date"], booked["time"])
    assert event.via == booked["via"] == "claude"
    assert [(item["type"], set(item)) for item in turns] == [
        (kind, {"type", "text"}) for kind in ("user", "assistant", "user", "assistant")
    ]
    assert (
        f"**{sum(b['party_size'] for b in live)} guests** in {len(live)} bookings"
        in turns[1]["text"]
    )
    assert [(b["date"], b["time"]) for b in DATA["blocked"]] == [(tomorrow, "13:00")]
    assert turns[3]["text"].startswith("Done. **13:00** is blocked tomorrow")
    assert kept == ["Remy Schmitt"]
    assert f"{kept[0]} keeps their table" in turns[3]["text"]


def test_moved_frames_keep_their_local_times_across_a_clock_change() -> None:
    frame = '{"date":"2026-10-31","starts_at":"2026-10-31T20:30:00+01:00","key":"k-R-009"}'

    assert demo_management.moved(frame, date(2027, 4, 10) - TODAY, TZ) == (
        '{"date":"2027-04-10","starts_at":"2027-04-10T20:30:00+02:00","key":"k-R-009"}'
    )
