"""Times of day as the contracts write them (`HH:MM`) and as minutes past midnight.

No import from the rest of `core`: `core.models` checks its services with it.
"""

from datetime import time


def to_minutes(hhmm: str) -> int:
    """`"19:30"` is 1170: the minutes past midnight of an `HH:MM` the models validated."""
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def from_minutes(minute: int) -> time:
    """The time of day `minute` minutes past midnight, the inverse of `to_minutes`."""
    return time(minute // 60, minute % 60)
