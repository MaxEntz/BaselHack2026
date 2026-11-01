"""Home's activity lines (#135), worded when read: a row that can't be worded is logged (#220)."""

import logging
from datetime import date
from zoneinfo import ZoneInfo

import pytest

from agent.activity import word

TZ = ZoneInfo("Europe/Zurich")
TODAY = date(2026, 10, 31)
GUEST = "Sara Muller"


def test_a_booking_line_names_the_booking_its_guest_and_its_time() -> None:
    details = {"guest": GUEST, "party": 2, "starts_at": "2026-10-31T18:30:00+01:00"}

    assert word("new_booking", "R-009", details, TODAY, TZ) == (
        "R-009, Sara Muller, 2 guests, today 18:30"
    )


@pytest.mark.parametrize(
    ("details", "error"),
    [
        ({"guest": GUEST, "party": 2}, "KeyError"),  # no start: an older writer
        ({"guest": GUEST, "party": "two", "starts_at": "2026-10-31T18:30:00+01:00"}, "ValueError"),
        ({"guest": GUEST, "party": 2, "starts_at": None}, "TypeError"),
    ],
)
def test_a_line_without_its_details_reads_as_its_reference_and_is_logged(
    details: dict[str, object], error: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger="agent.activity"):
        assert word("new_booking", "R-009", details, TODAY, TZ) == "R-009"

    [line] = caplog.records
    assert line.levelno == logging.WARNING
    assert (line.__dict__["kind"], line.__dict__["reservation"]) == ("new_booking", "R-009")
    # Home reads the row on every visit: its type, no traceback each time (#220).
    assert (line.__dict__["error"], line.exc_info) == (error, None)
    # Never the guest's name (§12): the line names the row, not what it holds.
    assert GUEST not in str(line.__dict__) and GUEST not in caplog.text
