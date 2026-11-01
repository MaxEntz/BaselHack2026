"""Merchant search with preference filters (tags, max price, city) and filter relaxation, and
which registry entries guests' calls may reach.

The gateway's MCP tools and the BATP node both call these rules, so the two modes agree by
construction. They are pure functions over the `live` merchants, which `search` loads from the
registry in one query: enough for the POC's handful of merchants (past ~100, filter in SQL with
`tags @> :tags`). Preferences only arrive as filters on each call; nothing is stored
(AGENTS.md §13).
"""

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date as Date
from typing import get_args

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from core.db import merchants_t
from core.errors import AgentUnavailable, InvalidInput
from core.models import (
    PUBLISHED_STATUSES,
    FilterSuggestion,
    MerchantProfile,
    MerchantSummary,
    PriceLevel,
    SearchFilters,
    SearchMerchantsOutput,
    Tag,
)
from core.text import strip_accents

log = logging.getLogger("agent.discovery")

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
# What separates two words once text is folded.
WORD_BREAK = re.compile(r"[^a-z0-9]+")

# Shorter words never identify a restaurant: "le" or "la" would match half the names.
MIN_QUERY_WORD = 3

# Words of a restaurant's name that don't identify it, because any request may use them (#89).
# A query names a merchant when it holds every *other* word of its name ("The Green Door":
# "green" and "door"), so a generic search ("a table for the four of us") stays generic.
NAME_NOISE = frozenset(
    {"restaurant", "restaurants", "resto", "table", "tables", "tonight", "today", "tomorrow"}
    | {"dinner", "lunch", "book", "booking", "reservation", "reserve", "soir", "diner", "midi"}
    # Kinds of places: "un petit restaurant" or "a little cafe" names no merchant.
    | {"petit", "petite", "petits", "maison", "chez", "bistro", "bistrot", "brasserie", "cafe"}
    | {"cuisine", "little", "house", "kitchen", "bar", "grill", "pizzeria", "trattoria"}
    | {"gasthaus", "gasthof", "wirtschaft", "stube", "hotel"}
    # Articles and connectors, in English, French and German.
    | {"the", "and", "for", "with", "les", "des", "une", "aux", "pour", "avec", "sur", "dans"}
    | {"der", "die", "das", "den", "dem", "und", "zum", "zur", "mit", "von", "bei", "auf"}
    # The tags' spellings: "restaurant avec terrasse" doesn't name La Terrasse.
    | {word for spelling in TAG_SPELLINGS for word in spelling.split("-")}
)
# `near` words that name no city: "near me" or "ici" means no city filter.
NEAR_NOISE = frozenset({"near", "nearby", "around", "close", "here", "me", "pres", "ici", "de"})

# Bounds on one search, so a request can't keep the gateway or the node busy (the contracts
# don't cap them). The gateway's tool and the BATP node share them, so both modes agree (#90).
MAX_QUERY = 500
MAX_TAGS = 32


@dataclass(frozen=True)
class RegistryEntry:
    """What routing a call needs to know about a merchant."""

    slug: str
    name: str
    status: str
    fallback_url: str

    @property
    def paused(self) -> bool:
        """A `down` merchant refuses guests' calls, with the link to its own booking page."""
        return self.status == "down"


def routable(entry: RegistryEntry | None) -> RegistryEntry | None:
    """The entry a guest's call may address: None for an unknown merchant and for one still
    `building`, which guests never see. A `paused` entry is routable: it answers with its
    fallback URL, and each adapter words that refusal itself (a `ToolError`, a BATP 503)."""
    if entry is None or entry.status == "building":
        return None
    return entry


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


def fold(text: str) -> str:
    """How user text is compared: case, accents and spacing ignored as in a guest's name
    (`guests.guest_key`), and ligatures spelled out ("Œuf" is "oeuf")."""
    spelled = strip_accents(text.casefold().translate(LIGATURES))
    return " ".join(spelled.split()).casefold()


def _tokens(text: str) -> list[str]:
    """The folded words of `text`, in order."""
    return [word for word in WORD_BREAK.split(fold(text)) if word]


def _words(text: str, min_length: int = 1) -> set[str]:
    return {word for word in _tokens(text) if len(word) >= min_length}


def normalize_tag(raw: str) -> Tag | None:
    """`"Végétarien"` → `"vegetarian"`, `"gluten_free"` → `"gluten-free"`; None if unknown."""
    return TAG_SPELLINGS.get("-".join(_tokens(raw)))


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
    return Criteria(frozenset(tags), max_price, city, fold(query)), ignored


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


def query_names(query: str, name: str, city: str | None) -> bool:
    """Whether `query` names the restaurant `name` in `city`.

    It must hold every distinctive word of the name: not a `NAME_NOISE` word, nor the
    restaurant's city ("Brasserie Mulhouse" isn't named by "a table in Mulhouse"). A name made
    only of such words ("La Terrasse") is named by the whole name, as a phrase.
    """
    distinctive = _words(name, MIN_QUERY_WORD) - NAME_NOISE - _words(city or "")
    if distinctive:
        return distinctive <= _words(query)
    phrase = " ".join(_tokens(name))
    return bool(phrase) and f" {phrase} " in f" {' '.join(_tokens(query))} "


def _named_in(merchants: Sequence[MerchantSummary], query: str) -> list[MerchantSummary]:
    return [m for m in merchants if query_names(query, m.name, m.city)]


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
    return sorted(found, key=lambda merchant: (fold(merchant.name), merchant.slug))


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


async def load_profile(slug: str, *, engine: AsyncEngine) -> MerchantProfile | None:
    """The merchant's profile as the registry stores it: its display copy, kept in step with the
    merchant's rows (#9). None if the slug is unknown; `ValidationError` if the stored copy no
    longer fits the contract (each caller decides whether to skip it)."""
    query = select(merchants_t.c.profile).where(merchants_t.c.slug == slug)
    async with engine.connect() as conn:
        stored = await conn.scalar(query)
    return None if stored is None else MerchantProfile.model_validate(stored)


async def _named_in_registry(query: str, engine: AsyncEngine) -> list[RegistryEntry]:
    """The live and `down` registry entries the query names (same rule as `search`), by slug."""
    if not _words(query, MIN_QUERY_WORD):
        return []
    registry = select(*_ENTRY_COLUMNS, merchants_t.c.city).where(
        merchants_t.c.status.in_(PUBLISHED_STATUSES)
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(registry.order_by(merchants_t.c.slug))).mappings().all()
    return [
        RegistryEntry(row["slug"], row["name"], row["status"], row["fallback_url"])
        for row in rows
        if query_names(query, row["name"], row["city"])
    ]


async def find_named_merchant(query: str, *, engine: AsyncEngine) -> RegistryEntry | None:
    """The merchant the query names, live or `down`; None if it names none.

    When discovery itself fails, the link to that merchant's own booking page is still
    something to give.
    """
    named = await _named_in_registry(query, engine)
    return named[0] if named else None


async def find_named_down_merchant(query: str, *, engine: AsyncEngine) -> RegistryEntry | None:
    """The `down` merchant the query names, unless a live merchant also matches it.

    `search` never shows a `down` merchant, and treats a query naming none of its merchants as
    a generic search: without this, "a table at Le Petit Zinc" during an outage would silently
    propose other restaurants instead of Le Petit Zinc's fallback link (AGENTS.md §9).
    """
    named = await _named_in_registry(query, engine)
    if not named or any(entry.status == "live" for entry in named):
        return None
    return named[0]


def check_search_bounds(query: str, filters: SearchFilters | None) -> None:
    """Refuse a search beyond `MAX_TAGS` or `MAX_QUERY` before it reads the registry."""
    tags = filters.tags if filters else ()
    if len(tags) > MAX_TAGS or len(query) > MAX_QUERY:
        raise InvalidInput(
            f"A search takes at most {MAX_TAGS} tags and a {MAX_QUERY}-character query"
        )


class MerchantPaused(AgentUnavailable):
    """A search whose query names only a `down` merchant: the guest gets its booking page.

    The gateway words it (`gateway.fallback.unavailable`), with `merchant`'s fallback URL.
    """

    def __init__(self, merchant: RegistryEntry) -> None:
        super().__init__(f"{merchant.name} is not taking bookings here right now")
        self.merchant = merchant


async def check_search(query: str, filters: SearchFilters | None, *, engine: AsyncEngine) -> None:
    """What the gateway checks before a search: its bounds, then that its query doesn't name only
    a `down` merchant (`find_named_down_merchant`), which is `MerchantPaused`."""
    check_search_bounds(query, filters)
    if down := await find_named_down_merchant(query, engine=engine):
        raise MerchantPaused(down)


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
    check_search_bounds(query, filters)
    criteria, ignored = normalize_filters(filters, near, query)
    merchants = await load_live_merchants(engine)
    return SearchMerchantsOutput(
        merchants=tuple(filter_merchants(merchants, criteria)),
        suggestions=tuple(suggest_relaxations(merchants, criteria)),
        ignored_filters=tuple(ignored),
    )
