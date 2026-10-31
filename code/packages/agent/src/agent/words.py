"""Days in the merchant's words, in English whatever the server's locale (`strftime`'s `%a` and
`%b` follow `LC_TIME`)."""

from datetime import date

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def short_day(day: date) -> str:
    """ "Sat 31 Oct", the mockup's day (`sesame-management.html`)."""
    return f"{WEEKDAYS[day.weekday()]} {day.day} {MONTHS[day.month - 1]}"
