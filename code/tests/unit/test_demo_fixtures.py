"""The 3 demo merchants (#10) allow every demo scenario of AGENTS.md §9."""

import html
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from core.models import MerchantProfile, PriceLevel

FIXTURES = Path(__file__).parents[1] / "fixtures"
SITES = Path(__file__).parents[2] / "sites"
PRICES: list[PriceLevel] = ["€", "€€", "€€€", "€€€€"]


def matching(
    profiles: dict[str, MerchantProfile], tags: set[str], max_price: PriceLevel | None = None
) -> set[str]:
    """Discovery's rules (#12): every requested tag, price at most `max_price`."""
    return {
        slug
        for slug, profile in profiles.items()
        if tags <= set(profile.tags)
        and (max_price is None or PRICES.index(profile.price_level) <= PRICES.index(max_price))
    }


@pytest.mark.parametrize("slug", ["le-petit-zinc", "la-graine-verte", "maison-albane"])
def test_fixture_round_trips(slug: str) -> None:
    data = json.loads((FIXTURES / f"{slug}.json").read_text())

    profile = MerchantProfile.model_validate(data)

    assert profile.slug == slug
    assert profile.model_dump(mode="json") == data
    assert profile.location is not None
    assert profile.location.city == "Mulhouse"
    assert profile.seats == sum(table.seats for table in profile.tables)


def test_floor_plans_and_group_limits(demo_profiles: dict[str, MerchantProfile]) -> None:
    plans = {
        slug: (Counter(table.seats for table in profile.tables), profile.max_party_size)
        for slug, profile in demo_profiles.items()
    }

    assert plans == {
        "le-petit-zinc": ({2: 4, 4: 6, 6: 2}, 6),
        "la-graine-verte": ({2: 6, 4: 3}, 4),
        "maison-albane": ({2: 5, 4: 3}, 6),
    }


def test_vegetarian_on_a_budget_excludes_maison_albane_by_price(
    demo_profiles: dict[str, MerchantProfile],
) -> None:
    assert matching(demo_profiles, {"vegetarian"}) == set(demo_profiles)
    assert matching(demo_profiles, {"vegetarian"}, "€€") == {"le-petit-zinc", "la-graine-verte"}


def test_vegan_with_terrace_finds_nothing_but_each_filter_dropped_does(
    demo_profiles: dict[str, MerchantProfile],
) -> None:
    assert matching(demo_profiles, {"vegan", "terrace"}) == set()
    # #12's suggestions: drop "vegan" -> 2 matches, drop "terrace" -> 1 match.
    assert len(matching(demo_profiles, {"terrace"})) == 2
    assert matching(demo_profiles, {"vegan"}) == {"la-graine-verte"}


def test_table_for_eight_at_le_petit_zinc_is_too_large(
    demo_profiles: dict[str, MerchantProfile],
) -> None:
    assert demo_profiles["le-petit-zinc"].max_party_size < 8


def page_text(slug: str, path: str) -> str:
    """The page's words as `builder.extract` compares them, without crawling it."""
    page = (SITES / slug / (path.lstrip("/") or "index.html")).read_text()
    visible = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.DOTALL)
    words = html.unescape(re.sub(r"<[^>]+>", " ", visible)).replace("\u2019", "'")
    return " ".join(words.split()).casefold()


@pytest.mark.parametrize("slug", ["le-petit-zinc", "la-graine-verte", "maison-albane"])
def test_what_each_merchants_agent_knows_is_on_its_site(
    slug: str, demo_profiles: dict[str, MerchantProfile]
) -> None:
    knowledge = demo_profiles[slug].knowledge

    assert knowledge is not None
    assert {"menu", "access", "practical", "about"} <= {item.topic for item in knowledge.items}
    for item in knowledge.items:
        assert item.source_url is not None
        assert item.quote is not None
        wanted = " ".join(item.quote.replace("\u2019", "'").split()).casefold()
        assert wanted in page_text(slug, urlsplit(str(item.source_url)).path), item.quote
