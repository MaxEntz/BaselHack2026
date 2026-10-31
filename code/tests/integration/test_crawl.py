"""The crawler (#21) in a real Chromium, against the demo site (#16) and small local sites."""

import base64
import json
import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.parse import urlsplit

import pytest

from builder.crawl import SHOT_MAX_HEIGHT, SHOT_WIDTH, CrawlResult, crawl
from builder.look import read_look
from core.config import get_settings
from core.errors import InvalidInput
from core.models import StepEvent

pytestmark = pytest.mark.browser

DEMO_SITE = Path(__file__).parents[2] / "sites" / "le-petit-zinc"
METADATA = "http://169.254.169.254/latest/meta-data/"


@dataclass
class Site:
    """A local site: its URL, the paths it was asked for, redirects and slow paths to serve.

    `later_redirects` answers normally the first time, then redirects.
    """

    url: str
    requests: list[str] = field(default_factory=list)
    redirects: dict[str, str] = field(default_factory=dict)
    later_redirects: dict[str, str] = field(default_factory=dict)
    slow: set[str] = field(default_factory=set)


class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, site: Site, **kwargs: Any) -> None:
        self.site = site
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        self.site.requests.append(self.path)
        if self.path in self.site.slow:
            time.sleep(3)
        location = self.site.redirects.get(self.path)
        if self.path in self.site.later_redirects and self.site.requests.count(self.path) > 1:
            location = self.site.later_redirects[self.path]
        if location:
            self.send_response(302)
            self.send_header("Location", location)
            self.end_headers()
            return
        super().do_GET()

    def log_message(self, format: str, *args: Any) -> None:
        pass


class _Server(ThreadingHTTPServer):
    def handle_error(self, request: Any, client_address: Any) -> None:
        pass  # the browser gave up on a slow page: a broken pipe


@pytest.fixture(scope="module")
def serve() -> Iterator[Callable[[Path], Site]]:
    """Serve a directory on a free port of 127.0.0.1."""
    servers: list[_Server] = []

    def start(directory: Path) -> Site:
        site = Site(url="")
        server = _Server(("127.0.0.1", 0), partial(_Handler, site=site, directory=str(directory)))
        site.url = f"http://127.0.0.1:{server.server_port}/"
        Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        servers.append(server)
        return site

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


class Steps(list[StepEvent]):
    async def __call__(self, event: StepEvent) -> None:
        self.append(event)


def write(directory: Path, files: dict[str, str]) -> None:
    for name, body in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"<!doctype html><html><head><meta charset=utf-8><title>{name}</title></head>"
            f"<body>{body}</body></html>",
            encoding="utf-8",
        )


def paths(result: CrawlResult) -> list[str]:
    return [urlsplit(page.url).path for page in result.pages]


def logged(caplog: pytest.LogCaptureFixture, event: str) -> list[str]:
    return [vars(r)["url"] for r in caplog.records if r.getMessage() == event]


@dataclass(frozen=True)
class DemoCrawl:
    result: CrawlResult
    steps: Steps
    seconds: float


@pytest.fixture(scope="module")
async def demo(serve: Callable[[Path], Site]) -> DemoCrawl:
    steps = Steps()
    started = time.monotonic()
    result = await crawl(serve(DEMO_SITE).url, steps, allow_private=True)
    return DemoCrawl(result, steps, time.monotonic() - started)


async def test_reads_the_demo_site_pages_from_its_homepage(demo: DemoCrawl) -> None:
    assert demo.seconds < 10
    assert paths(demo.result) == [
        "/",
        "/carte.html",
        "/horaires.html",
        "/contact.html",
        "/reservation.html",
    ]
    home = demo.result.pages[0]
    assert home.title == "Le Petit Zinc · Bistrot alsacien à Mulhouse"
    assert home.description and "Mulhouse" in home.description
    assert [block["@type"] for block in home.json_ld] == ["Restaurant"]
    assert "Choucroute" in demo.result.pages[1].text
    assert all(page.text and "  " not in page.text for page in demo.result.pages)


async def test_takes_each_pages_screenshot_with_its_text_boxes(demo: DemoCrawl) -> None:
    for page in demo.result.pages:
        assert page.shot is not None
        assert base64.b64decode(page.shot)[:2] == b"\xff\xd8"
        assert page.shot_width == SHOT_WIDTH
        assert 0 < page.shot_height <= SHOT_MAX_HEIGHT
    home = demo.result.pages[0]
    box = min(
        (box for box in home.boxes if "Le Petit Zinc" in box["text"]),
        key=lambda box: box["width"] * box["height"],
    )
    assert 0 <= box["x"] < SHOT_WIDTH
    assert 0 <= box["y"] < home.shot_height
    assert box["width"] > 0
    assert box["height"] > 0


async def test_keeps_the_ids_a_one_page_sites_link_can_land_on(
    serve: Callable[[Path], Site],
) -> None:
    result = await crawl(
        serve(DEMO_SITE.parent / "la-graine-verte").url, Steps(), allow_private=True
    )

    assert "reserver" in result.pages[0].anchors


async def test_reports_each_page_read_then_a_summary(demo: DemoCrawl) -> None:
    steps = demo.steps

    assert {step.step for step in steps} == {"crawl"}
    assert [(step.status, step.detail) for step in steps[:2]] == [
        ("running", "Loading homepage"),
        ("running", "Read homepage"),
    ]
    # The other pages load together, so they finish in any order.
    assert sorted(str(step.detail) for step in steps[2:-1]) == [
        "Read carte",
        "Read contact",
        "Read horaires",
        "Read reservation",
    ]
    assert steps[-1].status == "done"
    assert steps[-1].detail == (
        "Read 5 pages: /, /carte.html, /horaires.html, /contact.html, /reservation.html"
    )
    assert steps[-1].duration_ms is not None and steps[-1].duration_ms > 0


async def test_never_follows_a_link_to_another_host(
    serve: Callable[[Path], Site], tmp_path: Path
) -> None:
    site = serve(tmp_path)
    elsewhere = site.url.replace("127.0.0.1", "localhost")  # same server, another host
    write(
        tmp_path,
        {
            "index.html": f'<a href="{elsewhere}menu.html">Menu</a>'
            '<a href="https://example.com/menu">Menu</a><a href="carte.html">La carte</a>',
            "carte.html": "Choucroute",
            "menu.html": "Ailleurs",
        },
    )

    result = await crawl(site.url, Steps(), allow_private=True)

    assert paths(result) == ["/", "/carte.html"]
    assert "/menu.html" not in site.requests


async def test_respects_max_pages_and_truncates_the_text(
    serve: Callable[[Path], Site], tmp_path: Path
) -> None:
    site = serve(tmp_path)
    links = "".join(f'<a href="menu-{n}.html">Menu {n}</a>' for n in range(8))
    write(tmp_path, {"index.html": links + "<p>" + "Flammekueche " * 2000 + "</p>"})
    write(tmp_path, {f"menu-{n}.html": f"Plat {n} " * 2000 for n in range(8)})

    result = await crawl(site.url, Steps(), max_pages=3, max_chars_per_page=500, allow_private=True)

    assert paths(result) == ["/", "/menu-0.html", "/menu-1.html", "/menu-2.html"]
    assert all(0 < len(page.text) <= 500 for page in result.pages)
    assert not {f"/menu-{n}.html" for n in range(3, 8)} & set(site.requests)


async def test_a_missing_homepage_ends_the_step_with_an_error(
    serve: Callable[[Path], Site], tmp_path: Path
) -> None:
    url = f"{serve(tmp_path).url}missing.html"
    steps = Steps()

    with pytest.raises(InvalidInput, match="error 404"):
        await crawl(url, steps, allow_private=True)

    assert [step.status for step in steps] == ["running", "error"]
    assert steps[-1].detail == f"I couldn't open {url} (error 404) — is the address right?"


async def test_an_unreachable_homepage_ends_the_step_with_an_error() -> None:
    steps = Steps()

    with pytest.raises(InvalidInput, match="is the address right"):
        await crawl("http://nowhere.invalid/", steps, allow_private=True)

    assert [step.status for step in steps] == ["running", "error"]


async def test_refuses_a_private_address_unless_allowed(
    serve: Callable[[Path], Site], tmp_path: Path
) -> None:
    site = serve(tmp_path)
    steps = Steps()

    with pytest.raises(InvalidInput, match="private network address"):
        await crawl(site.url, steps, allow_private=False)

    assert site.requests == []
    assert [step.status for step in steps] == ["running", "error"]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Settings read from the environment set by the test."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused@localhost/unused")
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


async def test_crawl_allow_private_0_refuses_localhost(
    env: pytest.MonkeyPatch, serve: Callable[[Path], Site], tmp_path: Path
) -> None:
    env.setenv("CRAWL_ALLOW_PRIVATE", "0")
    site = serve(tmp_path)

    with pytest.raises(InvalidInput, match="private network address"):
        await crawl(site.url, Steps())

    assert site.requests == []


async def test_crawl_allow_private_1_reads_localhost(
    env: pytest.MonkeyPatch, serve: Callable[[Path], Site], tmp_path: Path
) -> None:
    env.setenv("CRAWL_ALLOW_PRIVATE", "1")
    write(tmp_path, {"index.html": "Bienvenue"})

    result = await crawl(serve(tmp_path).url, Steps())

    assert result.pages[0].text == "Bienvenue"


async def test_follows_a_redirect_and_resolves_links_from_where_it_lands(
    serve: Callable[[Path], Site], tmp_path: Path
) -> None:
    site = serve(tmp_path)
    site.redirects["/"] = "/fr/"
    write(tmp_path, {"fr/index.html": '<a href="carte.html">La carte</a>', "fr/carte.html": "Ok"})

    result = await crawl(site.url, Steps(), allow_private=True)

    assert result.website == site.url
    assert paths(result) == ["/fr/", "/fr/carte.html"]


async def test_a_page_redirecting_to_the_metadata_endpoint_is_skipped(
    serve: Callable[[Path], Site], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="builder.crawl")
    site = serve(tmp_path)
    site.redirects["/menu"] = METADATA
    write(tmp_path, {"index.html": '<a href="menu">Menu</a><a href="contact.html">Contact</a>'})

    result = await crawl(site.url, Steps(), allow_private=True)

    assert paths(result) == ["/"]
    assert logged(caplog, "crawl_request_refused") == [METADATA]
    assert sorted(logged(caplog, "crawl_page_skipped")) == [
        f"{site.url}contact.html",  # 404
        f"{site.url}menu",
    ]


async def test_a_homepage_redirecting_to_the_metadata_endpoint_is_refused(
    serve: Callable[[Path], Site], tmp_path: Path
) -> None:
    site = serve(tmp_path)
    site.redirects["/"] = METADATA

    with pytest.raises(InvalidInput, match="redirects to a private network address"):
        await crawl(site.url, Steps(), allow_private=True)


async def test_a_redirect_only_the_browser_gets_is_still_refused(
    serve: Callable[[Path], Site], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The server answers the check with a page, then the browser with a redirect."""
    caplog.set_level(logging.WARNING, logger="builder.crawl")
    site = serve(tmp_path)
    site.redirects["/"] = "/a.html"
    site.later_redirects["/a.html"] = METADATA
    write(tmp_path, {"a.html": "Rien à voir"})

    with pytest.raises(InvalidInput, match="redirects to a private network address"):
        await crawl(site.url, Steps(), allow_private=True)

    assert site.requests == ["/", "/a.html", "/a.html"]
    assert logged(caplog, "crawl_request_refused") == [METADATA]


async def test_reads_what_scripts_add_but_keeps_them_off_the_metadata_endpoint(
    serve: Callable[[Path], Site], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="builder.crawl")
    site = serve(tmp_path)
    script = f"""<script>
      setTimeout(() => document.body.insertAdjacentHTML(
        "beforeend", '<p>Ouvert le dimanche</p><a href="horaires.html">Horaires</a>'), 200);
      fetch("{METADATA}").then(
        () => document.body.append(" LEAKED"), () => document.body.append(" BLOCKED"));
    </script>"""
    write(tmp_path, {"index.html": script, "horaires.html": "12h-14h"})

    result = await crawl(site.url, Steps(), allow_private=True)

    assert paths(result) == ["/", "/horaires.html"]
    assert "Ouvert le dimanche" in result.pages[0].text
    assert "BLOCKED" in result.pages[0].text
    assert logged(caplog, "crawl_request_refused") == [METADATA]


async def test_a_slow_page_is_skipped_within_the_page_timeout(
    serve: Callable[[Path], Site], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="builder.crawl")
    site = serve(tmp_path)
    site.slow.add("/menu.html")
    write(tmp_path, {"index.html": '<a href="menu.html">Menu</a>', "menu.html": "Trop tard"})
    started = time.monotonic()

    result = await crawl(site.url, Steps(), page_timeout_s=1, allow_private=True)

    assert time.monotonic() - started < 3
    assert paths(result) == ["/"]
    assert logged(caplog, "crawl_page_skipped") == [f"{site.url}menu.html"]


# The homepage's look (#149): read from computed styles, the demo sites to the issue's values.

SITES = Path(__file__).parents[2] / "sites"


@pytest.mark.parametrize(
    ("site", "expected"),
    [
        (
            "la-graine-verte",
            {
                "bg": "#edebe2",
                "surface": "#f8f6ef",
                "line": "#d5d2c3",
                "ink": "#47522f",
                "muted": "#5f6a45",
                "accent": "#727f55",
                "accent_2": "#935046",
                "on_accent": "#f4f2ea",
                "display_font": "Fraunces Soft",
                "body_font": "Karla",
                "corners": "organic",
                "texture": "leather",
            },
        ),
        (
            "le-petit-zinc",
            {
                "bg": "#f7f2e8",
                "surface": "#fffaf0",
                "ink": "#2a2320",
                "accent": "#8f2d2a",
                "display_font": "serif",
                "corners": "rounded",
                "radius_px": 10,
            },
        ),
        (
            "maison-albane",
            {
                "bg": "#151413",
                "ink": "#efe9df",
                "accent": "#c8a96a",
                "display_font": "serif",
                "corners": "square",
            },
        ),
    ],
)
async def test_a_demo_sites_look_is_read_from_its_homepage(
    serve: Callable[[Path], Site], site: str, expected: dict[str, Any]
) -> None:
    result = await crawl(serve(SITES / site).url, Steps(), allow_private=True)

    look, _ = read_look(result.pages[0].look)
    assert {key: getattr(look, key) for key in expected} == expected
    assert all(page.look is None for page in result.pages[1:])
    # The demo fixtures store the same look: the seeded merchants have it too.
    stored = json.loads((Path(__file__).parents[1] / "fixtures" / f"{site}.json").read_text())
    assert stored["look"] == look.model_dump(mode="json")


async def test_the_crawler_never_loads_images_media_or_fonts(
    serve: Callable[[Path], Site], tmp_path: Path
) -> None:
    # Rule #21: only text and its styles are read; CSS is needed for the look (#149).
    (tmp_path / "style.css").write_text(
        '@font-face { font-family: "Brand"; src: url("brand.woff2"); }\n'
        'body { background: #123456; color: #fafafa; font-family: "Brand", serif; }\n'
        ".hero { background-image: url('hero.jpg'); height: 200px; }\n",
        encoding="utf-8",
    )
    (tmp_path / "index.html").write_text(
        "<!doctype html><html><head><meta charset=utf-8><title>Chez Test</title>"
        '<link rel="stylesheet" href="style.css"></head><body>'
        '<h1>Chez Test</h1><div class="hero"></div><img src="logo.png" alt="">'
        '<video src="clip.mp4" autoplay muted></video><p>Ouvert le soir.</p></body></html>',
        encoding="utf-8",
    )
    site = serve(tmp_path)

    result = await crawl(site.url, Steps(), allow_private=True)

    requested = set(site.requests)
    assert {"/", "/style.css"} <= requested
    assert requested.isdisjoint({"/brand.woff2", "/hero.jpg", "/logo.png", "/clip.mp4"})
    look, _ = read_look(result.pages[0].look)
    assert (look.bg, look.ink) == ("#123456", "#fafafa")
