"""search() on a real registry: only `live` merchants, the same rules as the pure functions."""

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import AsyncEngine

from core.db import merchants_t
from core.models import FilterSuggestion, SearchFilters
from gateway.discovery import search

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
