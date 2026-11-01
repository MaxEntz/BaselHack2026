"""Merchant slugs as the builder makes them: from the name, within `core.models.Slug`'s bounds."""

import re
import unicodedata
from urllib.parse import urlsplit

from core.models.base import SLUG_MAX, SLUG_MIN


def slugify(name: str, website: str) -> str:
    """Slug from the name, or from the site's host when the name gives under `SLUG_MIN`
    characters ("Bo", "寿司"). The builder still adds a suffix when the slug is taken."""
    host = (urlsplit(website).hostname or "").removeprefix("www.")
    slug = ""
    for source in (name, host, "restaurant"):
        ascii_text = unicodedata.normalize("NFKD", source).encode("ascii", "ignore").decode()
        slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:SLUG_MAX].rstrip("-")
        if len(slug) >= SLUG_MIN:
            break
    return slug


def numbered(base: str, number: int) -> str:
    """`base-2`, `base-3`…, cut so that the suffix fits in `SLUG_MAX`."""
    suffix = f"-{number}"
    return base[: SLUG_MAX - len(suffix)].rstrip("-") + suffix
