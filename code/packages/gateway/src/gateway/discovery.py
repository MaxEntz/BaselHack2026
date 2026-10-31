"""Merchant search with preference filters (tags, max price, city) and filter relaxation.

The rules are pure functions over the `live` merchants, which `search` loads from the registry
in one query: enough for the POC's handful of merchants (past ~100, filter in SQL with
`tags @> :tags`). Preferences only arrive as filters on each call; nothing is stored
(AGENTS.md §13).
"""

import logging
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date as Date
from typing import get_args

from pydantic import ValidationError
from sqlalchemy import RowMapping, select
from sqlalchemy.ext.asyncio import AsyncEngine

from core.db import merchants_t
from core.models import (
    FilterSuggestion,
    MerchantSummary,
    PriceLevel,
    SearchFilters,
    SearchMerchantsOutput,
    Tag,
)

log = logging.getLogger("gateway.discovery")

PRICE_ORDER: tuple[PriceLevel, ...] = get_args(PriceLevel)

# Canonical spelling (folded, words joined by "-") -> tag. Claude may write anything.
TAG_SPELLINGS: dict[str, Tag] = {tag: tag for tag in get_args(Tag)} | {
    "veggie": "vegetarian",
    "vegetarians": "vegetarian",
    "vegetarien": "vegetarian",
    "vegetariens": "vegetarian",
    "vegetarian-friendly": "vegetarian",
    "vegans": "vegan",
    "vegetalien": "vegan",
    "vegetaliens": "vegan",
    "plant-based": "vegan",
    "sans-gluten": "gluten-free",
    "gf": "gluten-free",
    "terrasse": "terrace",
    "outdoor": "terrace",
    "enfants": "kid-friendly",
    "kids": "kid-friendly",
    "family": "kid-friendly",
    "family-friendly": "kid-friendly",
}

# Letters NFKD does not decompose into a base letter plus accents.
LIGATURES = str.maketrans({"œ": "oe", "æ": "ae", "ø": "o", "ł": "l", "đ": "d"})

# Query words that name no restaurant: a generic search ("restaurant tonight") stays generic.
QUERY_NOISE = frozenset(
    {"restaurant", "restaurants", "resto", "table", "tables", "tonight", "today", "tomorrow"}
    | {"dinner", "lunch", "book", "booking", "reservation", "reserve", "soir", "diner", "midi"}
    # Common in names, but they don't identify one: "un petit restaurant" names no merchant.
    | {"petit", "petite", "petits", "maison", "chez", "bistro", "bistrot", "brasserie", "cafe"}
    | {"cuisine", "little", "house", "kitchen"}
)
# `near` words that name no city: "near me" or "ici" means no city filter.
NEAR_NOISE = frozenset({"near", "nearby", "around", "close", "here", "me", "pres", "ici", "de"})

# Shorter query words are ignored: "le" or "la" would match half the names.
MIN_QUERY_WORD = 3


@dataclass(frozen=True)
class RegistryEntry:
    """What routing a call needs to know about a merchant."""

    slug: str
    name: str
    status: str
    fallback_url: str


_ENTRY_COLUMNS = (
    merchants_t.c.slug,
    merchants_t.c.name,
    merchants_t.c.status,
    merchants_t.c.fallback_url,
)


@dataclass(frozen=True)
class Criteria:
    """Normalized search: tags to require, highest price, folded city, folded query."""

    tags: frozenset[Tag]
    max_price: PriceLevel | None
    city: str | None
    query: str


def _fold(text: str) -> str:
    """How user text is compared: lowercase, without accents, single spaces."""
    decomposed = unicodedata.normalize("NFKD", text.casefold().translate(LIGATURES))
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.split())


def _words(text: str, min_length: int = 1) -> set[str]:
    return {word for word in re.split(r"[^a-z0-9]+", _fold(text)) if len(word) >= min_length}


def normalize_tag(raw: str) -> Tag | None:
    """`"Végétarien"` → `"vegetarian"`, `"gluten_free"` → `"gluten-free"`; None if unknown."""
    canonical = "-".join(re.split(r"[^a-z0-9]+", _fold(raw))).strip("-")
    return TAG_SPELLINGS.get(canonical)


def normalize_filters(
    raw: SearchFilters | None, near: str | None, query: str
) -> tuple[Criteria, list[str]]:
    """Criteria from what Claude sent, plus the tags we don't know (reported, not fatal)."""
    tags: set[Tag] = set()
    ignored: list[str] = []
    reported: set[str] = set()
    for raw_tag in raw.tags if raw else ():
        tag = normalize_tag(raw_tag)
        if tag is not None:
            tags.add(tag)
        elif raw_tag.strip() and raw_tag not in reported:
            reported.add(raw_tag)
            ignored.append(raw_tag)
    near_words = _words(near or "") - NEAR_NOISE
    city = " ".join(sorted(near_words)) if near_words else None
    max_price = raw.max_price if raw else None
    return Criteria(frozenset(tags), max_price, city, _fold(query)), ignored


def _in_city(merchant: MerchantSummary, city: str | None) -> bool:
    # Every word of the merchant's city appears in `near`: "MULHOUSE, France" or
    # "centre de Mulhouse" match Mulhouse, Bâle doesn't, and a merchant without a city never
    # matches a city filter.
    if city is None:
        return True
    merchant_words = _words(merchant.city or "") - NEAR_NOISE
    return bool(merchant_words) and merchant_words <= _words(city)


def _within_price(merchant: MerchantSummary, max_price: PriceLevel | None) -> bool:
    if max_price is None:
        return True
    return PRICE_ORDER.index(merchant.price_level) <= PRICE_ORDER.index(max_price)


def _named_in(merchants: Sequence[MerchantSummary], query: str) -> list[MerchantSummary]:
    words = _words(query, MIN_QUERY_WORD) - QUERY_NOISE
    return [m for m in merchants if words & _words(m.name, MIN_QUERY_WORD)]


def filter_merchants(
    merchants: Sequence[MerchantSummary], criteria: Criteria
) -> list[MerchantSummary]:
    """Merchants with every requested tag, within price and city, sorted by name.

    A query naming merchants ("zinc") restricts to them; any other query ("restaurant
    tonight") is a generic search and is ignored.
    """
    pool = _named_in(merchants, criteria.query) or merchants
    found = [
        merchant
        for merchant in pool
        if criteria.tags <= set(merchant.tags)
        and _within_price(merchant, criteria.max_price)
        and _in_city(merchant, criteria.city)
    ]
    return sorted(found, key=lambda merchant: (_fold(merchant.name), merchant.slug))


def suggest_relaxations(
    merchants: Sequence[MerchantSummary], criteria: Criteria
) -> list[FilterSuggestion]:
    """When nothing matches: each filter whose removal alone finds merchants, best first.

    `drop` is `"query"` (when the query named merchants), a canonical tag (`terrasse` is
    reported as `terrace`), `"max_price"` or `"near"`. On a tie, dropping the query comes
    first: it is the least deliberate restriction.
    """
    if filter_merchants(merchants, criteria):
        return []
    candidates: list[tuple[str, Criteria]] = []
    if _named_in(merchants, criteria.query):
        candidates.append(("query", replace(criteria, query="")))
    candidates += [
        (tag, replace(criteria, tags=criteria.tags - {tag})) for tag in sorted(criteria.tags)
    ]
    if criteria.max_price is not None:
        candidates.append(("max_price", replace(criteria, max_price=None)))
    if criteria.city is not None:
        candidates.append(("near", replace(criteria, city=None)))
    counted = [
        FilterSuggestion(drop=name, matches=len(filter_merchants(merchants, relaxed)))
        for name, relaxed in candidates
    ]
    return sorted((s for s in counted if s.matches > 0), key=lambda s: -s.matches)


async def load_live_merchants(engine: AsyncEngine) -> list[MerchantSummary]:
    """Every merchant the protocol may show: `building` and `down` stay hidden.

    A registry row that no longer fits the contract is logged and skipped, so one bad row
    never breaks every search.
    """
    query = select(
        merchants_t.c.slug,
        merchants_t.c.name,
        merchants_t.c.city,
        merchants_t.c.tags,
        merchants_t.c.price_level,
        merchants_t.c.website,
    ).where(merchants_t.c.status == "live")
    async with engine.connect() as conn:
        rows = (await conn.execute(query)).mappings().all()
    merchants = []
    for row in rows:
        try:
            merchants.append(MerchantSummary.model_validate(dict(row)))
        except ValidationError as error:
            log.warning(
                "registry row skipped", extra={"slug": row["slug"], "errors": error.errors()}
            )
    return merchants


async def find_merchant(slug: str, *, engine: AsyncEngine) -> RegistryEntry | None:
    """The merchant's registry entry, whatever its status; None if the slug is unknown."""
    query = select(*_ENTRY_COLUMNS).where(merchants_t.c.slug == slug)
    async with engine.connect() as conn:
        row = (await conn.execute(query)).mappings().one_or_none()
    return RegistryEntry(**row) if row else None


async def _named_in_registry(query: str, engine: AsyncEngine) -> list[RowMapping]:
    """The live and `down` registry entries the query names, by slug."""
    words = _words(query, MIN_QUERY_WORD) - QUERY_NOISE
    if not words:
        return []
    registry = select(*_ENTRY_COLUMNS).where(merchants_t.c.status.in_(("live", "down")))
    async with engine.connect() as conn:
        rows = (await conn.execute(registry.order_by(merchants_t.c.slug))).mappings().all()
    return [row for row in rows if words & _words(row["name"], MIN_QUERY_WORD)]


async def find_named_merchant(query: str, *, engine: AsyncEngine) -> RegistryEntry | None:
    """The merchant the query names, live or `down`: when discovery itself fails, the link to
    its own booking page is still something to give."""
    named = await _named_in_registry(query, engine)
    return RegistryEntry(**named[0]) if named else None


async def find_named_down_merchant(query: str, *, engine: AsyncEngine) -> RegistryEntry | None:
    """The `down` merchant the query names, unless a live merchant also matches it.

    `search` never shows a `down` merchant, and treats a query naming none of its merchants as
    a generic search: without this, "a table at Le Petit Zinc" during an outage would silently
    propose other restaurants instead of Le Petit Zinc's fallback link (AGENTS.md §9).
    """
    named = await _named_in_registry(query, engine)
    if not named or any(row["status"] == "live" for row in named):
        return None
    return RegistryEntry(**named[0])


async def search(
    query: str,
    near: str | None,
    date: Date | None,
    filters: SearchFilters | None,
    *,
    engine: AsyncEngine,
) -> SearchMerchantsOutput:
    """`search_merchants`: the live merchants matching the user's preferences.

    `date` is accepted but not used to filter yet: the model has no closing days (POC).
    """
    criteria, ignored = normalize_filters(filters, near, query)
    merchants = await load_live_merchants(engine)
    return SearchMerchantsOutput(
        merchants=tuple(filter_merchants(merchants, criteria)),
        suggestions=tuple(suggest_relaxations(merchants, criteria)),
        ignored_filters=tuple(ignored),
    )
