"""The only definition of a guest: bookings, changes, lookups and the Guests page use it (#77)."""

from core.text import strip_accents


def guest_key(name: str) -> str:
    """A guest name ignoring case, accents and spacing: Claude may not repeat it verbatim.

    Spacing is any Unicode whitespace (a non-breaking space too), case is `casefold` ("Weiß"
    is "weiss") and accents are dropped ("Müller" is "muller", #144), none of which SQL's
    `lower` and `\\s` match.
    """
    return " ".join(strip_accents(name).split()).casefold()


def same_guest(given: str, booked: str) -> bool:
    """Whether the name `given` now is the guest `booked` under: equal `guest_key`s."""
    return guest_key(given) == guest_key(booked)
