"""Discovery rules (#12) on the 3 demo merchants: preference filters and their relaxation."""

from unittest.mock import MagicMock

import pytest

from core.errors import InvalidInput
from core.models import (
    FilterSuggestion,
    MerchantProfile,
    MerchantSummary,
    SearchFilters,
)
from gateway.discovery import (
    MAX_QUERY,
    MAX_TAGS,
    Criteria,
    check_search_bounds,
    filter_merchants,
    normalize_filters,
    search,
    suggest_relaxations,
)


@pytest.fixture
def merchants(demo_profiles: dict[str, MerchantProfile]) -> list[MerchantSummary]:
    return [
        MerchantSummary(
            slug=profile.slug,
            name=profile.name,
            city=profile.location.city if profile.location else None,
            tags=profile.tags,
            price_level=profile.price_level,
            website=profile.website,
        )
        for profile in demo_profiles.values()
    ]


def find(
    merchants: list[MerchantSummary],
    tags: list[str] | None = None,
    max_price: str | None = None,
    near: str | None = None,
    query: str = "",
) -> tuple[list[str], list[FilterSuggestion], list[str]]:
    filters = SearchFilters.model_validate({"tags": tags or [], "max_price": max_price})
    criteria, ignored = normalize_filters(filters, near, query)
    found = filter_merchants(merchants, criteria)
    return [m.name for m in found], suggest_relaxations(merchants, criteria), ignored


def test_no_filters_returns_every_merchant_by_name(merchants: list[MerchantSummary]) -> None:
    names, suggestions, ignored = find(merchants)

    assert names == ["La Graine Verte", "Le Petit Zinc", "Maison Albane"]
    assert (suggestions, ignored) == ([], [])


def test_vegetarian_on_a_budget_excludes_maison_albane_by_price(
    merchants: list[MerchantSummary],
) -> None:
    names, _, _ = find(merchants, tags=["vegetarian"], max_price="€€")

    assert names == ["La Graine Verte", "Le Petit Zinc"]


def test_price_is_compared_in_order(merchants: list[MerchantSummary]) -> None:
    assert find(merchants, max_price="€")[0] == ["La Graine Verte"]
    assert find(merchants, max_price="€€€€")[0] == [
        "La Graine Verte",
        "Le Petit Zinc",
        "Maison Albane",
    ]


def test_every_requested_tag_is_required(merchants: list[MerchantSummary]) -> None:
    assert find(merchants, tags=["vegan"])[0] == ["La Graine Verte"]
    assert find(merchants, tags=["vegetarian", "terrace"])[0] == ["Le Petit Zinc", "Maison Albane"]


def test_vegan_with_terrace_suggests_which_filter_to_drop(
    merchants: list[MerchantSummary],
) -> None:
    names, suggestions, _ = find(merchants, tags=["vegan", "terrace"])

    assert names == []
    assert suggestions == [
        FilterSuggestion(drop="vegan", matches=2),
        FilterSuggestion(drop="terrace", matches=1),
    ]


def test_suggestions_cover_price_and_skip_drops_that_still_find_nothing(
    merchants: list[MerchantSummary],
) -> None:
    # Terrace + €: La Graine Verte is € without a terrace; the others have one but cost more.
    names, suggestions, _ = find(merchants, tags=["vegetarian", "terrace"], max_price="€")

    assert names == []
    assert suggestions == [
        FilterSuggestion(drop="max_price", matches=2),
        FilterSuggestion(drop="terrace", matches=1),
    ]


def test_no_suggestions_when_something_matches(merchants: list[MerchantSummary]) -> None:
    assert find(merchants, tags=["vegan"])[1] == []


@pytest.mark.parametrize(
    ("raw", "tag"),
    [
        ("Végétarien", "vegetarian"),
        ("veggie", "vegetarian"),
        ("vegetarian-friendly", "vegetarian"),
        ("végétalien", "vegan"),
        ("Plant-based", "vegan"),
        ("sans gluten", "gluten-free"),
        ("Gluten free", "gluten-free"),
        ("GF", "gluten-free"),
        ("terrasse", "terrace"),
        ("outdoor", "terrace"),
        ("enfants", "kid-friendly"),
        ("family", "kid-friendly"),
        ("Kid friendly", "kid-friendly"),
        (" HALAL ", "halal"),
        ("gluten_free", "gluten-free"),
        ("gluten\u2011free", "gluten-free"),
        ("sans-gluten", "gluten-free"),
        ("kid_friendly", "kid-friendly"),
        ("family-friendly", "kid-friendly"),
        ("kids", "kid-friendly"),
        ("végétariens", "vegetarian"),
        ("Vegetarians", "vegetarian"),
    ],
)
def test_tags_are_normalized(raw: str, tag: str) -> None:
    criteria, ignored = normalize_filters(SearchFilters(tags=(raw,)), None, "")

    assert criteria.tags == frozenset({tag})
    assert ignored == []


def test_unknown_tags_are_ignored_and_reported(merchants: list[MerchantSummary]) -> None:
    names, _, ignored = find(merchants, tags=["michelin", "vegan", "rooftop"])

    assert names == ["La Graine Verte"]
    assert ignored == ["michelin", "rooftop"]


@pytest.mark.parametrize("near", ["Mulhouse", "mulhouse", "MULHOUSE, France", "centre de Mulhouse"])
def test_city_matches_case_and_accent_insensitively(
    merchants: list[MerchantSummary], near: str
) -> None:
    assert len(find(merchants, near=near)[0]) == 3


def test_another_city_finds_nothing_and_suggests_dropping_it(
    merchants: list[MerchantSummary],
) -> None:
    names, suggestions, _ = find(merchants, near="Bâle")

    assert names == []
    assert suggestions == [FilterSuggestion(drop="near", matches=3)]


def test_city_accents_are_ignored() -> None:
    criteria, _ = normalize_filters(None, "Zürich", "")

    assert criteria.city == "zurich"


@pytest.mark.parametrize(
    ("query", "names"),
    [
        ("zinc", ["Le Petit Zinc"]),
        ("Table at Maison Albane tonight", ["Maison Albane"]),
        ("restaurant tonight", ["La Graine Verte", "Le Petit Zinc", "Maison Albane"]),
        # Words under 3 letters are ignored, or "le" would pick Le Petit Zinc.
        ("le", ["La Graine Verte", "Le Petit Zinc", "Maison Albane"]),
    ],
)
def test_query_restricts_to_merchants_named_by_it(
    merchants: list[MerchantSummary], query: str, names: list[str]
) -> None:
    assert find(merchants, query=query)[0] == names


def test_query_alone_never_triggers_suggestions(merchants: list[MerchantSummary]) -> None:
    assert find(merchants, query="zinc")[1] == []


def test_criteria_is_immutable() -> None:
    criteria, _ = normalize_filters(None, None, "")

    assert criteria == Criteria(tags=frozenset(), max_price=None, city=None, query="")
    with pytest.raises(AttributeError):
        criteria.query = "x"  # type: ignore[misc]


def summary(slug: str, name: str, city: str | None = "Mulhouse") -> MerchantSummary:
    return MerchantSummary(
        slug=slug,
        name=name,
        city=city,
        tags=(),
        price_level="€€",
        website=f"http://{slug}.example/",
    )


def test_unknown_tags_are_reported_once_and_blanks_dropped() -> None:
    _, ignored = normalize_filters(SearchFilters(tags=("michelin", "", " ", "michelin")), None, "")

    assert ignored == ["michelin"]


@pytest.mark.parametrize("near", [None, "", "   ", "-", "near me", "ici"])
def test_near_without_a_city_name_is_no_filter(
    merchants: list[MerchantSummary], near: str | None
) -> None:
    names, suggestions, _ = find(merchants, near=near)

    assert len(names) == 3
    assert suggestions == []


@pytest.mark.parametrize("city", [None, "", "-"])
def test_a_merchant_without_a_city_never_matches_a_city_filter(city: str | None) -> None:
    criteria, _ = normalize_filters(None, "Bâle", "")

    assert filter_merchants([summary("nowhere", "Nowhere", city=city)], criteria) == []


def test_city_with_accents_and_joining_words() -> None:
    merchants = [summary("zur", "Z", city="Zürich"), summary("vil", "V", city="Villeneuve-de-Berg")]

    def names(near: str) -> list[str]:
        return [m.name for m in filter_merchants(merchants, normalize_filters(None, near, "")[0])]

    assert names("zurich") == ["Z"]
    assert names("Villeneuve-de-Berg") == ["V"]


def test_ligatures_fold_to_their_letters() -> None:
    merchants = [summary("boeuf", "Le Bœuf Rouge"), summary("other", "Autre")]

    criteria, _ = normalize_filters(None, None, "boeuf")

    assert [m.slug for m in filter_merchants(merchants, criteria)] == ["boeuf"]


def test_generic_words_never_restrict_the_query() -> None:
    merchants = [summary("parc", "Restaurant du Parc"), summary("zinc", "Le Petit Zinc")]

    criteria, _ = normalize_filters(None, None, "restaurant tonight")

    assert len(filter_merchants(merchants, criteria)) == 2


def test_an_incidental_query_word_can_be_dropped(merchants: list[MerchantSummary]) -> None:
    # "albane" names Maison Albane, which is not vegan; La Graine Verte is.
    names, suggestions, _ = find(merchants, tags=["vegan"], query="albane")

    assert names == []
    assert suggestions == [
        FilterSuggestion(drop="query", matches=1),
        FilterSuggestion(drop="vegan", matches=1),
    ]


def test_a_generic_query_word_does_not_hide_a_match(merchants: list[MerchantSummary]) -> None:
    names, _, _ = find(merchants, tags=["vegan"], query="cuisine maison")

    assert names == ["La Graine Verte"]


def test_equal_names_are_ordered_by_slug() -> None:
    merchants = [summary("b-zinc", "Zinc"), summary("a-zinc", "Zinc")]

    criteria, _ = normalize_filters(None, None, "")

    assert [m.slug for m in filter_merchants(merchants, criteria)] == ["a-zinc", "b-zinc"]


@pytest.mark.parametrize("query", ["un petit restaurant végétarien", "une maison", "a little cafe"])
def test_generic_words_of_merchant_names_keep_the_search_generic(
    query: str, merchants: list[MerchantSummary]
) -> None:
    criteria, _ = normalize_filters(None, None, query)

    assert len(filter_merchants(merchants, criteria)) == len(merchants)


@pytest.mark.parametrize(
    ("query", "slug"), [("Le Petit Zinc", "le-petit-zinc"), ("Maison Albane", "maison-albane")]
)
def test_the_distinctive_word_still_names_the_merchant(
    query: str, slug: str, merchants: list[MerchantSummary]
) -> None:
    criteria, _ = normalize_filters(None, None, query)

    assert [m.slug for m in filter_merchants(merchants, criteria)] == [slug]


def test_a_search_at_its_bounds_is_accepted() -> None:
    check_search_bounds("a" * MAX_QUERY, SearchFilters(tags=("vegan",) * MAX_TAGS))


@pytest.mark.parametrize(
    ("query", "filters"),
    [("a" * (MAX_QUERY + 1), None), ("dinner", SearchFilters(tags=("vegan",) * (MAX_TAGS + 1)))],
)
async def test_a_search_beyond_its_bounds_is_invalid_before_reading_the_registry(
    query: str, filters: SearchFilters | None
) -> None:
    engine = MagicMock()

    with pytest.raises(InvalidInput, match=f"at most {MAX_TAGS} tags"):
        await search(query, None, None, filters, engine=engine)

    assert not engine.mock_calls
