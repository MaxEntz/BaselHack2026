"""Profiles generated from 3 real websites (#75, AGENTS.md §11 M4) stay valid profiles."""

import json
from pathlib import Path

import pytest

from core.models import MerchantProfile

GENERATED = Path(__file__).parents[1] / "fixtures" / "generated"
SERVICES = {
    "restaurant-kronenhalle": [("12:00", "22:30")],
    "restaurant-kunsthalle": [("11:30", "23:00")],
    "brasserie-volkshaus-basel": [("19:00", "22:00")],
}


@pytest.mark.parametrize("slug", sorted(SERVICES))
def test_a_generated_profile_validates_and_keeps_what_its_site_states(slug: str) -> None:
    profile = MerchantProfile.model_validate(
        json.loads((GENERATED / f"{slug}.json").read_text(encoding="utf-8"))
    )

    assert profile.slug == slug
    assert [(s.start, s.end) for s in profile.services] == SERVICES[slug]
    assert profile.seats == sum(table.seats for table in profile.tables)


def test_every_generated_profile_is_listed() -> None:
    assert {path.stem for path in GENERATED.glob("*.json")} == SERVICES.keys()
