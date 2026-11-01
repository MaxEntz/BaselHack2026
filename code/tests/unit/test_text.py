"""strip_accents: the one way names, searches and crawled links ignore accents (#220)."""

import pytest

from core.text import strip_accents


@pytest.mark.parametrize(
    ("text", "bare"),
    [
        ("Réserver", "Reserver"),
        ("Müller", "Muller"),
        ("Zoé Huber", "Zoe Huber"),
        ("Öffnungszeiten", "Offnungszeiten"),
        ("ﬁne", "fine"),  # a compatibility ligature is spelled out
        ("Œuf", "Œuf"),  # a letter NFKD keeps whole stays
        ("Weiß", "Weiß"),  # case is the caller's: `casefold` makes it "weiss"
        ("", ""),
    ],
)
def test_strip_accents(text: str, bare: str) -> None:
    assert strip_accents(text) == bare
