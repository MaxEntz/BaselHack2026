"""The only definition of a guest: bookings, changes, lookups and the Guests page use it (#77)."""

import unicodedata


def guest_key(name: str) -> str:
    """A guest name ignoring case, accents and spacing: Claude may not repeat it verbatim.

    Spacing is any Unicode whitespace (a non-breaking space too), case is `casefold` ("Weiß"
    is "weiss") and accents are dropped ("Müller" is "muller", #144), none of which SQL's
    `lower` and `\\s` match.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    bare = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(bare.split()).casefold()


def same_guest(given: str, booked: str) -> bool:
    return guest_key(given) == guest_key(booked)
