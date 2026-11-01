"""How the builder and the platform word lists for the merchant."""

from collections.abc import Sequence


def joined(words: Sequence[str]) -> str:
    """`Monday`, `Monday and Tuesday`, `Monday, Tuesday and Sunday`: `words` isn't empty."""
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"
