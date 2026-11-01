"""The demo website (#16) stays consistent with the Le Petit Zinc fixture and with the crawl."""

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from builder.urls import USEFUL_LINK
from core.models import MerchantProfile

SITES = Path(__file__).parents[2] / "sites"
SITE = SITES / "le-petit-zinc"
WEEK = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}


class Page(HTMLParser):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.links: list[str] = []
        self.assets: list[str] = []
        self.json_ld: list[Any] = []
        self._script: list[str] | None = None
        self.feed(path.read_text(encoding="utf-8"))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"] or "")
        elif tag in ("link", "script") and (attributes.get("href") or attributes.get("src")):
            self.assets.append(attributes.get("href") or attributes.get("src") or "")
        if tag == "script" and attributes.get("type") == "application/ld+json":
            self._script = []

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script is not None:
            self.json_ld.append(json.loads("".join(self._script)))
            self._script = None


def local_files(page: Page) -> set[str]:
    targets = (urlsplit(url) for url in [*page.links, *page.assets])
    return {target.path for target in targets if not target.scheme and target.path}


def test_every_local_link_points_to_a_file() -> None:
    for path in SITES.glob("*/*.html"):
        for target in local_files(Page(path)):
            assert (path.parent / target).is_file(), f"{path.relative_to(SITES)} -> {target}"


def test_homepage_links_to_the_pages_the_crawl_reads() -> None:
    links = {urlsplit(href).path for href in Page(SITE / "index.html").links}

    crawled = {link for link in links if USEFUL_LINK.search(link)}

    assert {"carte.html", "horaires.html", "contact.html", "reservation.html"} <= crawled


def test_json_ld_matches_the_fixture(profile_data: dict[str, Any]) -> None:
    profile = MerchantProfile.model_validate(profile_data)

    (restaurant,) = Page(SITE / "index.html").json_ld

    assert restaurant["@type"] == "Restaurant"
    assert restaurant["name"] == profile.name
    assert restaurant["priceRange"] == profile.price_level
    assert restaurant["address"]["addressLocality"] == profile_data["location"]["city"]
    assert restaurant["url"] == str(profile.website)
    assert restaurant["acceptsReservations"] == str(profile.fallback_url)
    hours = restaurant["openingHoursSpecification"]
    assert [(h["opens"], h["closes"]) for h in hours] == [
        (service.start, service.end) for service in profile.services
    ]
    # The model has no closing day: the site must not announce one.
    assert all(set(h["dayOfWeek"]) == WEEK for h in hours)


def test_fallback_url_is_the_booking_form(profile_data: dict[str, Any]) -> None:
    page = SITE / urlsplit(profile_data["fallback_url"]).path.lstrip("/")

    assert "booking.js" in Page(page).assets


def test_booking_form_matches_the_fixture(profile_data: dict[str, Any]) -> None:
    script = (SITE / "booking.js").read_text(encoding="utf-8")

    services = re.findall(r'start: "(\d\d:\d\d)", end: "(\d\d:\d\d)"', script)

    assert services == [(s["start"], s["end"]) for s in profile_data["services"]]
    assert f"/public/{profile_data['slug']}/reservations" in script


def test_site_has_no_vegan_dish() -> None:
    # A "vegan" preference must find no match here (AGENTS.md §9).
    text = " ".join(path.read_text(encoding="utf-8") for path in SITE.glob("*.html")).lower()

    assert not re.search(r"vegan|végan|végétalien|vegetalien|végétal", text)


def test_menu_marks_diets_and_prices_mains_at_mid_range() -> None:
    menu = (SITE / "carte.html").read_text(encoding="utf-8")
    mains = menu.split('id="plats"')[1].split("</section>")[0]

    prices = [int(price) for price in re.findall(r'class="dish-price">(\d+) €', mains)]

    assert "(V)" in menu
    assert "(SG)" in menu
    assert (min(prices), max(prices)) == (16, 24)


def test_la_graine_verte_keeps_what_the_builder_reads(
    demo_profiles: dict[str, MerchantProfile],
) -> None:
    """#148 restyled the page; the facts of its fixture stay word for word."""
    page = (SITES / "la-graine-verte" / "index.html").read_text(encoding="utf-8")
    profile = demo_profiles["la-graine-verte"]

    for fact in (
        f"Tables de 1 à {profile.max_party_size} personnes.",
        "03 89 00 00 11",
        "Ouvert tous les jours.",
        "11h30 \u2013 14h30",  # en dashes, as on the page
        "18h30 \u2013 21h30",
        "12 €",
        "restaurant fictif",
    ):
        assert fact in page
    assert str(profile.fallback_url).endswith("#reserver")
    assert 'id="reserver"' in page


def test_self_hosted_fonts_ship_with_their_licence() -> None:
    fonts = list(SITES.glob("*/fonts/*.woff2"))

    assert fonts
    for font in fonts:
        family = font.stem.split("-")[0].capitalize()
        assert (font.parent / f"OFL-{family}.txt").is_file(), font.relative_to(SITES)
