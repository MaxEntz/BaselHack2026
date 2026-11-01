"""The crawler's pure parts (#21): addresses, request filtering, link choice, text."""

import pytest

from builder.crawl import crawl, normalize_text, parse_json_ld
from builder.guard import HostGuard, is_refused
from builder.urls import normalize_url, page_key, page_name, same_site, useful_links
from core.errors import InvalidInput
from core.models import StepEvent

HOME = "https://www.bistrot.example/"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://Example.COM", "https://example.com/"),
        ("  http://localhost:8080/carte.html#plats ", "http://localhost:8080/carte.html"),
        ("HTTPS://example.com/?lang=fr", "https://example.com/?lang=fr"),
        ("http://[::1]:8080", "http://[::1]:8080/"),
    ],
)
def test_normalize_url(url: str, expected: str) -> None:
    assert normalize_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/menu",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "example.com",
        "http://",
        "http://example.com:99999/",
        "http://[::1/",
    ],
)
def test_normalize_url_refuses_anything_but_an_http_address(url: str) -> None:
    with pytest.raises(InvalidInput, match="http:// or https://"):
        normalize_url(url)


def test_normalize_url_refuses_credentials() -> None:
    with pytest.raises(InvalidInput, match="user name or password"):
        normalize_url("http://admin:secret@example.com/")


@pytest.mark.parametrize(
    ("address", "allow_private", "refused"),
    [
        ("1.1.1.1", False, False),
        ("2606:4700:4700::1111", False, False),
        ("::ffff:1.1.1.1", False, False),
        ("127.0.0.1", False, True),
        ("127.0.0.1", True, False),
        ("::1", False, True),
        ("::1", True, False),
        ("::ffff:127.0.0.1", False, True),
        ("10.0.0.8", False, True),
        ("192.168.1.20", True, False),
        ("100.64.0.1", False, True),
        ("fd00::1", False, True),
        # Never, even in dev: cloud metadata, link-local, multicast, "any".
        ("169.254.169.254", True, True),
        ("::ffff:169.254.169.254", True, True),
        ("fe80::1", True, True),
        ("224.0.0.1", True, True),
        ("0.0.0.0", True, True),
        ("::", True, True),
    ],
)
def test_is_refused(address: str, allow_private: bool, refused: bool) -> None:
    assert is_refused(address, allow_private=allow_private) is refused


async def test_the_guard_checks_hosts_after_dns_resolution() -> None:
    guard = HostGuard(allow_private=False, timeout_s=5)

    assert await guard.refusal("http://localhost:8080/") == "private"
    assert await guard.refusal("http://127.0.0.1/") == "private"
    assert await guard.refusal("http://169.254.169.254/latest/meta-data/") == "private"
    assert await guard.refusal("http://nowhere.invalid/") == "unreachable"
    assert await guard.refusal("ftp://example.com/") == "unreachable"


async def test_the_guard_refuses_ports_other_than_80_and_443_without_allow_private() -> None:
    """On the team server its own public address is global: other ports are its services."""
    guard = HostGuard(allow_private=False, timeout_s=5)

    assert await guard.refusal("http://1.1.1.1:22/") == "port"
    assert await guard.refusal("http://1.1.1.1:5432/") == "port"
    assert await guard.refusal("https://1.1.1.1:4710/") == "port"
    assert await guard.refusal("https://1.1.1.1:8443/") == "port"
    assert await guard.refusal("http://1.1.1.1:80/") is None
    assert await guard.refusal("https://1.1.1.1:443/") is None
    assert await guard.refusal("https://1.1.1.1/") is None
    # The DNS verdict is per host; the port is checked on every URL, so it can't be reused.
    assert await guard.refusal("https://1.1.1.1:9000/") == "port"
    assert await guard.refusal("http://1.1.1.1:99999/") == "unreachable"


async def test_a_website_on_another_port_is_refused_before_any_browser() -> None:
    steps: list[StepEvent] = []

    async def emit(step: StepEvent) -> None:
        steps.append(step)

    with pytest.raises(InvalidInput, match="usual ports"):
        await crawl("https://1.1.1.1:8443/", emit, allow_private=False)

    assert [step.status for step in steps] == ["running", "error"]


async def test_the_guard_allows_any_port_in_dev() -> None:
    guard = HostGuard(allow_private=True, timeout_s=5)

    assert await guard.refusal("http://localhost:8080/") is None
    assert await guard.refusal("http://1.1.1.1:8081/") is None


async def test_the_guard_lets_localhost_through_in_dev_but_not_the_metadata_endpoint() -> None:
    guard = HostGuard(allow_private=True, timeout_s=5)

    assert await guard.refusal("http://localhost:8080/") is None
    assert await guard.refusal("http://169.254.169.254/") == "private"


def test_useful_links_keeps_same_site_pages_named_like_useful_ones() -> None:
    links = [
        ("https://www.bistrot.example/la-carte", "Nos plats"),  # path
        ("https://www.bistrot.example/p/42", "Réserver une table"),  # text, with an accent
        ("https://bistrot.example/contact#plan", "Contact"),  # without www, fragment dropped
        ("http://www.bistrot.example/contact", "Nous écrire"),  # the same page again
        ("https://www.bistrot.example/galerie", "Photos"),  # not a useful page
        ("https://other.example/menu", "Menu"),  # another site
        ("https://www.bistrot.example:8443/menu", "Menu"),  # another port
        ("mailto:resa@bistrot.example", "Réservations"),  # not a page
        ("https://www.bistrot.example/#menu", "Menu"),  # the homepage itself
        ("https://www.bistrot.example/r%C3%A9servation", "Plus"),  # percent-encoded accent
    ]

    assert useful_links(links, HOME, 5) == [
        "https://www.bistrot.example/la-carte",
        "https://www.bistrot.example/p/42",
        "https://bistrot.example/contact",
        "https://www.bistrot.example/r%C3%A9servation",
    ]


def test_a_trailing_slash_or_a_default_port_is_the_same_page() -> None:
    # One page key for the crawl and the extraction (#220).
    assert page_key(f"{HOME}menu/") == page_key("https://bistrot.example:443/menu#plats")
    assert page_key(f"{HOME}menu?lang=de") != page_key(f"{HOME}menu")
    # A default port only for its own scheme: plain HTTP on the TLS port is a dead link.
    assert page_key("http://bistrot.example:80/menu") == page_key(f"{HOME}menu")
    assert page_key("http://bistrot.example:443/menu") != page_key(f"{HOME}menu")
    assert not same_site("http://bistrot.example:443/x", HOME)
    assert same_site("http://bistrot.example/x", HOME)
    assert not same_site("https://www.bistrot.example:8443/menu", HOME)
    assert useful_links([(f"{HOME}menu/", "Menu"), (f"{HOME}menu", "Menu")], HOME, 5) == [
        f"{HOME}menu/"
    ]


def test_useful_links_reads_german_sites_too() -> None:
    links = [
        (f"{HOME}de/speisekarte/", "Speisekarte"),
        (f"{HOME}de/p/7", "Öffnungszeiten"),
        (f"{HOME}de/kontakt/", "Kontakt"),
        (f"{HOME}de/kunst/", "Kunst"),
    ]

    assert useful_links(links, HOME, 5) == [
        f"{HOME}de/speisekarte/",
        f"{HOME}de/p/7",
        f"{HOME}de/kontakt/",
    ]


def test_useful_links_stops_at_max_pages() -> None:
    links = [(f"{HOME}menu-{n}", "") for n in range(9)]

    assert useful_links(links, HOME, 3) == [f"{HOME}menu-0", f"{HOME}menu-1", f"{HOME}menu-2"]
    assert useful_links(links, HOME, 0) == []


@pytest.mark.parametrize(
    ("url", "name"),
    [
        ("http://localhost:8080/carte.html", "carte"),
        ("https://bistrot.example/fr/la-carte/", "la-carte"),
        ("https://bistrot.example/r%C3%A9server", "réserver"),
        ("https://bistrot.example/?page=menu", "page=menu"),
    ],
)
def test_page_name(url: str, name: str) -> None:
    assert page_name(url) == name


def test_normalize_text_collapses_spaces_and_blank_lines() -> None:
    text = "  Le  Petit\u00a0Zinc \n\n\n\n Ouvert\t tous les jours \n \n"

    assert normalize_text(text, 8000) == "Le Petit Zinc\n\nOuvert tous les jours"


def test_normalize_text_truncates() -> None:
    assert normalize_text("Choucroute " * 100, 50) == ("Choucroute " * 100)[:50]
    assert normalize_text("x " * 100, 51) == "x " * 25 + "x"


def test_parse_json_ld_keeps_objects_and_ignores_broken_blocks() -> None:
    blocks = ['{"@type": "Restaurant"}', "{not json", '[{"@type": "Menu"}, 3]', None, '"x"']

    assert parse_json_ld(blocks) == [{"@type": "Restaurant"}, {"@type": "Menu"}]


# The look's sample (#149, #197 review): bounded in size and in time.


def test_the_look_sample_is_bounded_before_it_is_cached() -> None:
    from builder.crawl import LOOK_KEY_CHARS, LOOK_KEYS, bounded_look

    huge = {f"rgb({i}, 0, 0)" + "x" * 500: float(i) for i in range(1000)}
    sample = bounded_look(
        {
            "bg": "rgb(1, 2, 3)" + "y" * 5000,
            "ink": "rgb(4, 5, 6)",
            "body_font": "z" * 5000,
            "heading": {"size": 10, "color": "rgb(0, 0, 0)", "font": "w" * 5000},
            "backgrounds": huge,
            "texts": huge,
            "borders": "not a map",
            "radii": [[1, 2, 3]] * 2000,
            "page_area": 1,
        }
    )

    assert len(sample["bg"]) == LOOK_KEY_CHARS
    assert len(sample["backgrounds"]) == len(sample["texts"]) == LOOK_KEYS
    assert all(len(key) <= 2 * LOOK_KEY_CHARS for key in sample["backgrounds"])
    assert min(sample["backgrounds"].values()) == 1000 - LOOK_KEYS  # the heaviest kept
    assert sample["borders"] == {}
    assert len(sample["radii"]) == 500
    assert len(sample["body_font"]) == len(sample["heading"]["font"]) == 200


async def test_a_look_that_takes_too_long_is_given_up(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from builder import crawl as crawl_module

    class SlowTab:
        async def evaluate(self, _: str) -> dict[str, object]:
            await asyncio.sleep(5)
            return {}

    monkeypatch.setattr(crawl_module, "LOOK_TIMEOUT_S", 0.05)

    assert await crawl_module._styles(SlowTab()) is None
