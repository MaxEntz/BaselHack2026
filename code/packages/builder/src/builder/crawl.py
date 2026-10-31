"""Playwright crawl of the merchant website (homepage + relevant same-domain pages).

The crawler loads an address the merchant typed (OWASP SSRF prevention), so every request
the browser makes is checked after DNS resolution: pages, redirect hops, scripts, fetches,
frames. Only public addresses pass, plus private ones with `CRAWL_ALLOW_PRIVATE=1` (the demo
site runs locally). Link-local addresses, such as the cloud metadata endpoint
169.254.169.254, never do. Known limit: Chromium resolves the host again after the check, so
DNS rebinding is not covered.
"""

import asyncio
import ipaddress
import json
import logging
import re
import socket
import time
import unicodedata
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Literal
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from playwright.async_api import BrowserContext, Route, WebSocketRoute, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from core.config import get_settings
from core.errors import InvalidInput
from core.models import StepEvent

log = logging.getLogger("builder.crawl")

StepEmitter = Callable[[StepEvent], Awaitable[None]]

# AGENTS.md §8.3: the secondary pages worth reading, matched on a link's path or text, in
# English, French and German (Speisekarte, Öffnungszeiten, Kontakt: most Basel sites).
USEFUL_LINK = re.compile(
    r"menu|carte|karte|hours|horaires|zeiten|contact|kontakt|book|reserv", re.IGNORECASE
)
# Longest wait for `networkidle` after `domcontentloaded`: some sites are never idle.
SETTLE_S = 2.0
MAX_REDIRECTS = 5
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
# Only the text is read: skip what is heavy and never holds any.
SKIPPED_RESOURCES = frozenset({"image", "media", "font"})
# Element ids kept per page, in document order: enough for a one-page site's sections.
MAX_ANCHORS = 200

Refusal = Literal["private", "unreachable"]

_EXTRACT = """() => ({
  title: document.title,
  text: document.body ? document.body.innerText : "",
  description:
    document.querySelector('meta[name="description" i]')?.getAttribute("content") ?? null,
  ids: Array.from(document.querySelectorAll("[id]"), (element) => element.id).filter(Boolean),
  jsonLd: Array.from(
    document.querySelectorAll('script[type="application/ld+json" i]'),
    (script) => script.textContent,
  ),
  links: Array.from(document.querySelectorAll("a[href]"), (a) => {
    try {
      return [new URL(a.getAttribute("href"), document.baseURI).href, a.textContent ?? ""];
    } catch {
      return null;
    }
  }).filter(Boolean),
})"""


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    text: str  # normalized visible text, truncated
    json_ld: list[dict[str, Any]]  # schema.org blocks found on the page
    description: str | None = None  # meta[name=description]
    # Element ids, in document order: where a link like `/#reserver` can land
    anchors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CrawlResult:
    website: str  # the merchant's address, normalized
    pages: list[Page]  # the homepage, then the pages read, in link order


class _Unreadable(Exception):
    """The page can't be read: an error status, too many redirects…"""


class _Blocked(_Unreadable):
    """A redirect leads to an address the guard refuses."""


# Request filtering


class HostGuard:
    """Decides, per host and after DNS resolution, whether the browser may connect to it."""

    def __init__(self, *, allow_private: bool, timeout_s: float) -> None:
        self.allow_private = allow_private
        self.timeout_s = timeout_s
        self._verdicts: dict[str, Refusal | None] = {}

    async def refusal(self, url: str) -> Refusal | None:
        """Why `url` must not be loaded, or None when it may be."""
        parts = urlsplit(url)
        host = parts.hostname
        if parts.scheme not in ("http", "https") or not host:
            return "unreachable"
        if host not in self._verdicts:
            self._verdicts[host] = await self._resolve(host)
        return self._verdicts[host]

    async def allows(self, url: str) -> bool:
        """Whether `url` may be loaded; a private address is logged."""
        refusal = await self.refusal(url)
        if refusal == "private":
            log.warning("crawl_request_refused", extra={"url": url})
        return refusal is None

    async def _resolve(self, host: str) -> Refusal | None:
        try:
            infos = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM),
                self.timeout_s,
            )
        except (OSError, UnicodeError, TimeoutError):
            return "unreachable"
        # Every address must pass: the browser may connect to any of them.
        if any(is_refused(str(info[4][0]), allow_private=self.allow_private) for info in infos):
            return "private"
        return None


def is_refused(address: str, *, allow_private: bool) -> bool:
    """Whether the crawler must not connect to this IP address."""
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 is 127.0.0.1, which Python 3.12 doesn't see
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return True  # not even in dev: 169.254.169.254 is the cloud metadata endpoint
    return not (ip.is_global or allow_private)


async def _route(guard: HostGuard, timeout_ms: float, route: Route) -> None:
    """Send a request through the guard, following its redirects here, hop by hop, and give
    the browser only the final answer.

    Chromium doesn't route the hops of a redirect it follows, so it must never get one: a
    public page could otherwise redirect it to a private address.
    """
    request = route.request
    try:
        if request.resource_type in SKIPPED_RESOURCES:
            await route.abort("blockedbyclient")
            return
        url = request.url
        for _ in range(MAX_REDIRECTS + 1):
            if not await guard.allows(url):
                await route.abort("blockedbyclient")
                return
            response = await route.fetch(url=url, max_redirects=0, timeout=timeout_ms)
            location = response.headers.get("location")
            if response.status not in REDIRECT_STATUSES or not location:
                await route.fulfill(response=response)
                return
            if request.method != "GET":
                break  # pages are read with GET; not worth replaying a body
            url = urljoin(url, location)
        await route.abort("failed")  # too many redirects, or a redirected POST
    except PlaywrightError as error:
        log.debug("crawl_request_failed", extra={"url": request.url, "error": error.message})
        with suppress(PlaywrightError):
            await route.abort("failed")


async def _refuse_web_socket(web_socket: WebSocketRoute) -> None:
    """Never connected: `_route` doesn't see WebSockets, and the text never comes from them."""
    await web_socket.close()


# The crawl


async def crawl(
    url: str,
    on_event: StepEmitter,
    *,
    max_pages: int = 5,
    page_timeout_s: float = 10,
    max_chars_per_page: int = 8000,
    allow_private: bool | None = None,
) -> CrawlResult:
    """Read the homepage and up to `max_pages` useful pages of the same site: the `crawl` step.

    Emits `running` events, then `done`, or `error` with a message for the merchant before
    raising: `InvalidInput` when the address is unusable or the homepage can't be read. A page
    other than the homepage that fails is skipped. `allow_private` defaults to
    `CRAWL_ALLOW_PRIVATE`.
    """
    started = time.monotonic()
    await on_event(StepEvent(step="crawl", status="running", detail="Loading homepage"))
    try:
        if allow_private is None:
            allow_private = get_settings().crawl_allow_private
        result = await _crawl(
            url,
            on_event,
            max_pages=max_pages,
            timeout_s=page_timeout_s,
            max_chars=max_chars_per_page,
            allow_private=allow_private,
        )
    except InvalidInput as error:
        await on_event(_finished("error", str(error), started))
        raise
    except Exception:
        await on_event(
            _finished("error", "Something went wrong while reading the website.", started)
        )
        raise
    names = ", ".join(["homepage", *(page_name(page.url) for page in result.pages[1:])])
    count = len(result.pages)
    await on_event(_finished("done", f"{count} page{'s' * (count > 1)} read: {names}", started))
    return result


async def _crawl(
    url: str,
    on_event: StepEmitter,
    *,
    max_pages: int,
    timeout_s: float,
    max_chars: int,
    allow_private: bool,
) -> CrawlResult:
    website = normalize_url(url)
    guard = HostGuard(allow_private=allow_private, timeout_s=timeout_s)
    match await guard.refusal(website):
        case "private":
            raise InvalidInput(f"I can't read {website}: it points to a private network address.")
        case "unreachable":
            raise InvalidInput(f"I couldn't open {website} — is the address right?")
    async with async_playwright() as playwright, await playwright.chromium.launch() as browser:
        # Requests from service workers would bypass the routes below.
        context = await browser.new_context(service_workers="block")
        await context.route("**/*", partial(_route, guard, timeout_s * 1000))
        await context.route_web_socket("**/*", _refuse_web_socket)
        try:
            home, links = await _read(
                context, guard, website, timeout_s=timeout_s, max_chars=max_chars
            )
        except (PlaywrightError, TimeoutError, _Unreadable) as error:
            if isinstance(error, _Blocked) or (
                isinstance(error, PlaywrightError) and "ERR_BLOCKED_BY_CLIENT" in error.message
            ):
                raise InvalidInput(
                    f"I can't read {website}: it redirects to a private network address."
                ) from None
            raise InvalidInput(
                f"I couldn't open {website} ({_reason(error, timeout_s)}) — is the address right?"
            ) from None
        await on_event(StepEvent(step="crawl", status="running", detail="Read homepage"))
        pages = await asyncio.gather(
            *(
                _read_page(context, guard, link, on_event, timeout_s=timeout_s, max_chars=max_chars)
                for link in useful_links(links, home.url, max_pages)
            )
        )
    return CrawlResult(website=website, pages=[home, *(page for page in pages if page)])


async def _read_page(
    context: BrowserContext,
    guard: HostGuard,
    url: str,
    on_event: StepEmitter,
    *,
    timeout_s: float,
    max_chars: int,
) -> Page | None:
    try:
        page, _ = await _read(context, guard, url, timeout_s=timeout_s, max_chars=max_chars)
    except (PlaywrightError, TimeoutError, _Unreadable) as error:
        log.warning("crawl_page_skipped", extra={"url": url, "error": _reason(error, timeout_s)})
        return None
    await on_event(StepEvent(step="crawl", status="running", detail=f"Read {page_name(page.url)}"))
    return page


async def _read(
    context: BrowserContext, guard: HostGuard, url: str, *, timeout_s: float, max_chars: int
) -> tuple[Page, list[list[str]]]:
    """Load `url` in a new tab within `timeout_s`: the page and its links as (href, text)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    tab = await context.new_page()
    try:
        async with asyncio.timeout_at(deadline):
            url = await _landing(context, guard, url, timeout_s * 1000)
            response = await tab.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
            if response is not None and not response.ok:
                raise _Unreadable(f"error {response.status}")
            # Let scripts fill the page, keeping a second of the budget to read it.
            settle_s = min(SETTLE_S, deadline - loop.time() - 1)
            if settle_s > 0:
                with suppress(PlaywrightTimeout):
                    await tab.wait_for_load_state("networkidle", timeout=settle_s * 1000)
            raw = await tab.evaluate(_EXTRACT)
            final_url = tab.url
    finally:
        with suppress(PlaywrightError):
            await tab.close()
    page = Page(
        url=final_url,
        title=" ".join(str(raw["title"]).split()),
        text=normalize_text(str(raw["text"]), max_chars),
        json_ld=parse_json_ld(raw["jsonLd"]),
        description=" ".join(str(raw["description"] or "").split()) or None,
        anchors=[str(anchor) for anchor in raw["ids"][:MAX_ANCHORS]],
    )
    return page, raw["links"]


async def _landing(context: BrowserContext, guard: HostGuard, url: str, timeout_ms: float) -> str:
    """Where `url` lands after its redirects, each hop checked by the guard.

    The tab then loads that address: the page keeps its real URL, which relative links resolve
    from, and the browser never follows a redirect itself (see `_route`).
    """
    for _ in range(MAX_REDIRECTS + 1):
        if not await guard.allows(url):
            raise _Blocked("it redirects to a private network address")
        response = await context.request.get(url, max_redirects=0, timeout=timeout_ms)
        location = response.headers.get("location")
        await response.dispose()
        if response.status not in REDIRECT_STATUSES or not location:
            return url
        url = urljoin(url, location)
    raise _Unreadable("too many redirects")


def _reason(error: Exception, timeout_s: float) -> str:
    if isinstance(error, TimeoutError | PlaywrightTimeout):
        return f"no answer within {timeout_s:g} s"
    if isinstance(error, PlaywrightError):
        # "net::ERR_NAME_NOT_RESOLVED at https://…" and the like: keep the code.
        match = re.search(r"net::(ERR_\w+)", error.message)
        return match[1] if match else "the browser couldn't load it"
    return str(error)


def _finished(status: Literal["done", "error"], detail: str, started: float) -> StepEvent:
    duration_ms = round((time.monotonic() - started) * 1000)
    return StepEvent(step="crawl", status=status, detail=detail, duration_ms=duration_ms)


# Pure helpers


def normalize_url(url: str) -> str:
    """`url` with a path and no fragment; `InvalidInput` unless it is an HTTP(S) address."""
    unusable = InvalidInput(
        f"I can only read a website address starting with http:// or https://, not {url[:200]!r}."
    )
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        raise unusable from None
    host = parts.hostname
    if parts.scheme.lower() not in ("http", "https") or not host:
        raise unusable
    if parts.username is not None or parts.password is not None:
        raise InvalidInput("The website address must not contain a user name or password.")
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc += f":{port}"
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))


def useful_links(links: Sequence[Sequence[str]], home: str, max_pages: int) -> list[str]:
    """Links to pages of the same site whose path or text names a useful page, in order,
    without fragment or duplicates, `max_pages` at most."""
    site = _site(home)
    seen = {_page_key(home)}
    found: list[str] = []
    for href, text in links:
        if len(found) >= max_pages:
            break
        parts = urlsplit(href)
        if parts.scheme not in ("http", "https") or _site(href) != site or _page_key(href) in seen:
            continue
        if USEFUL_LINK.search(_fold(unquote(parts.path))) or USEFUL_LINK.search(_fold(text)):
            seen.add(_page_key(href))
            found.append(urlunsplit(parts._replace(fragment="")))
    return found


def page_name(url: str) -> str:
    """Short name of a page for the chat: its last path segment, without extension."""
    parts = urlsplit(url)
    segments = [segment for segment in unquote(parts.path).split("/") if segment]
    if not segments:
        return parts.query or parts.netloc
    return re.sub(r"\.\w{2,5}$", "", segments[-1])


def normalize_text(text: str, max_chars: int) -> str:
    """Collapse runs of spaces and of blank lines, then cut at `max_chars`."""
    lines = (" ".join(line.split()) for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text[:max_chars].rstrip()


def parse_json_ld(blocks: Sequence[str | None]) -> list[dict[str, Any]]:
    """The JSON objects of `ld+json` scripts; a block that isn't valid JSON is ignored."""
    found: list[dict[str, Any]] = []
    for block in blocks:
        try:
            data = json.loads(block or "")
        except ValueError:
            continue
        found.extend(
            item for item in (data if isinstance(data, list) else [data]) if isinstance(item, dict)
        )
    return found


def _page_key(url: str) -> tuple[tuple[str, int | None], str, str]:
    """What tells two pages apart: `/contact` and `https://www.…/contact#map` are one page."""
    parts = urlsplit(url)
    return _site(url), parts.path or "/", parts.query


def _site(url: str) -> tuple[str, int | None]:
    """Host without `www.`, and port unless the default: `http://x` and `https://www.x` match."""
    parts = urlsplit(url)
    port = parts.port
    return (parts.hostname or "").removeprefix("www."), None if port in (80, 443) else port


def _fold(text: str) -> str:
    """Without accents, so that "Réserver" matches `reserv`."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
