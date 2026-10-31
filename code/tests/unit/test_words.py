from datetime import date

import pytest

from agent.words import short_day


@pytest.mark.parametrize(
    ("day", "words"),
    [
        (date(2026, 10, 31), "Sat 31 Oct"),
        (date(2026, 11, 2), "Mon 2 Nov"),
        (date(2027, 1, 3), "Sun 3 Jan"),
    ],
)
def test_short_day_is_the_mockups_day(day: date, words: str) -> None:
    assert short_day(day) == words
