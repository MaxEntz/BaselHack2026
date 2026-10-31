"""search() on a real registry: only `live` merchants, the same rules as the pure functions."""

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import AsyncEngine

from core.db import merchants_t
from core.models import FilterSuggestion, SearchFilters
from gateway.discovery import (
    RegistryEntry,
    find_merchant,
    find_named_down_merchant,
    find_named_merchant,
    search,
)

pytestmark = pytest.mark.integration


def registry_row(slug: str, name: str, **overrides: Any) -> dict[str, Any]:
    return {
        "slug": slug,
        "name": name,
        "website": f"http://{slug}.example/",
        "fallback_url": f"http://{slug}.example/book",
        "city": "Mulhouse",
        "tags": [],
        "price_level": "€€",
        "status": "live",
        "profile": {},
        **overrides,
    }


@pytest.fixture
async def token(engine: AsyncEngine) -> AsyncIterator[str]:
    """A word only this test's merchants have in their name: the query isolates them from
    whatever else the shared session database holds."""
    token = f"zq{uuid4().hex[:6]}"
    rows = [
        registry_row(f"{token}-a", f"Alpha {token}", tags=["vegan"], price_level="€"),
        registry_row(f"{token}-b", f"Bravo {token}", status="down"),
        registry_row(f"{token}-c", f"Charlie {token}", status="building"),
        registry_row(f"{token}-d", f"Delta {token}", tags=["terrace"], price_level="€€€"),
        # Not a contract tag: this row is skipped, the others are still found.
        registry_row(f"{token}-e", f"Echo {token}", tags=["rooftop"]),
    ]
    async with engine.begin() as conn:
        await conn.execute(insert(merchants_t), rows)
    yield token
    async with engine.begin() as conn:
        await conn.execute(delete(merchants_t).where(merchants_t.c.slug.startswith(token)))


async def test_search_only_returns_live_merchants(engine: AsyncEngine, token: str) -> None:
    result = await search(token, None, None, None, engine=engine)

    assert [m.name for m in result.merchants] == [f"Alpha {token}", f"Delta {token}"]
    assert str(result.merchants[0].website) == f"http://{token}-a.example/"
    assert (result.suggestions, result.ignored_filters) == ((), ())


async def test_search_reports_ignored_tags_and_suggestions(engine: AsyncEngine, token: str) -> None:
    filters = SearchFilters(tags=("vegan", "terrasse", "michelin"))

    result = await search(token, "Mulhouse", None, filters, engine=engine)

    assert result.merchants == ()
    assert result.suggestions == (
        FilterSuggestion(drop="terrace", matches=1),
        FilterSuggestion(drop="vegan", matches=1),
    )
    assert result.ignored_filters == ("michelin",)


async def test_find_merchant_returns_what_routing_needs(engine: AsyncEngine, token: str) -> None:
    slug = f"{token}-zinc"
    async with engine.begin() as conn:
        await conn.execute(insert(merchants_t).values(registry_row(slug, f"Zinc {token}")))

    entry = await find_merchant(slug, engine=engine)

    assert entry == RegistryEntry(slug, f"Zinc {token}", "live", f"http://{slug}.example/book")
    assert await find_merchant(f"{token}-unknown", engine=engine) is None


async def test_find_named_down_merchant(engine: AsyncEngine, token: str) -> None:
    down = registry_row(f"{token}-down", f"Zinc{token} Bistro", status="down")
    building = registry_row(f"{token}-build", f"Build{token} Bistro", status="building")
    # A live merchant sharing a word with a down one wins: the query may mean it.
    shared_down = registry_row(f"{token}-shared-down", f"Shared{token} Down", status="down")
    shared_live = registry_row(f"{token}-shared-live", f"Shared{token} Live", status="live")
    async with engine.begin() as conn:
        await conn.execute(insert(merchants_t), [down, building, shared_down, shared_live])

    async def named(query: str) -> str | None:
        entry = await find_named_down_merchant(query, engine=engine)
        return entry.slug if entry else None

    assert await named(f"a table at zinc{token} tonight") == f"{token}-down"
    assert await named(f"build{token}") is None
    assert await named(f"shared{token}") is None
    assert await named("restaurant tonight") is None
    assert await named("") is None


async def test_a_generic_word_of_a_down_merchants_name_does_not_name_it(
    engine: AsyncEngine, token: str
) -> None:
    down = registry_row(f"{token}-petit", f"Le Petit Zinc{token}", status="down")
    async with engine.begin() as conn:
        await conn.execute(insert(merchants_t), [down])

    named = await find_named_down_merchant(f"Le Petit Zinc{token}", engine=engine)

    assert named is not None
    assert await find_named_down_merchant("un petit restaurant végétarien", engine=engine) is None


async def test_find_named_merchant_is_live_or_down_but_not_building(
    engine: AsyncEngine, token: str
) -> None:
    rows = [
        registry_row(f"{token}-live", f"Live{token} Bistro"),
        registry_row(f"{token}-off", f"Off{token} Bistro", status="down"),
        registry_row(f"{token}-new", f"New{token} Bistro", status="building"),
    ]
    async with engine.begin() as conn:
        await conn.execute(insert(merchants_t), rows)

    async def named(query: str) -> str | None:
        entry = await find_named_merchant(query, engine=engine)
        return entry.slug if entry else None

    assert await named(f"a table at live{token}") == f"{token}-live"
    assert await named(f"off{token}") == f"{token}-off"
    assert await named(f"new{token}") is None
    assert await named("un petit restaurant") is None
