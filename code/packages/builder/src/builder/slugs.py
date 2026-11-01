"""Merchant slugs as the builder makes them: from the name, within `core.models.Slug`."""

import re
import unicodedata
from typing import get_args
from urllib.parse import urlsplit

from pydantic import StringConstraints

from core.models import Slug


def _bounds() -> tuple[int, int]:
    """The shortest and longest slug `Slug` accepts, read from its pattern (`{3,61}`): the
    limit has one definition. 61, so that `m_` + slug fits a Postgres identifier (63 bytes)."""
    constraints = next(arg for arg in get_args(Slug) if isinstance(arg, StringConstraints))
    found = re.search(r"\{(\d+),(\d+)\}", str(constraints.pattern))
    if found is None:
        raise RuntimeError("core.models.Slug no longer gives its length as {min,max}")
    return int(found[1]), int(found[2])


MIN_SLUG, MAX_SLUG = _bounds()


def slugify(name: str, website: str) -> str:
    """Slug from the name, or from the site's host when the name gives under `MIN_SLUG`
    characters ("Bo", "寿司"). The builder still adds a suffix when the slug is taken."""
    host = (urlsplit(website).hostname or "").removeprefix("www.")
    slug = ""
    for source in (name, host, "restaurant"):
        ascii_text = unicodedata.normalize("NFKD", source).encode("ascii", "ignore").decode()
        slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:MAX_SLUG].rstrip("-")
        if len(slug) >= MIN_SLUG:
            break
    return slug


def numbered(base: str, number: int) -> str:
    """`base-2`, `base-3`…, cut so that the suffix fits in `MAX_SLUG`."""
    suffix = f"-{number}"
    return base[: MAX_SLUG - len(suffix)].rstrip("-") + suffix
