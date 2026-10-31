"""Playwright crawl of the merchant website (homepage + relevant same-domain pages).

The crawler loads an address the merchant typed (OWASP SSRF prevention), so every request
the browser makes is checked after DNS resolution: pages, redirect hops, scripts, fetches,
frames. Only public addresses pass, plus private ones with `CRAWL_ALLOW_PRIVATE=1` (the demo
site runs locally). Link-local addresses, such as the cloud metadata endpoint
169.254.169.254, never do. Known limit: Chromium resolves the host again after the check, so
DNS rebinding is not covered.
"""

import asyncio
import base64
import ipaddress
import json
import logging
import math
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
# Each page's screenshot, for Studio's public tab (#151): the frame's width, bounded so that
# it fits in a chat stream and in a row. Without the images and fonts above.
SHOT_WIDTH = 1000
SHOT_MAX_HEIGHT = 6000
SHOT_QUALITY = 60
SHOT_MAX_BYTES = 1_200_000
SHOT_TIMEOUT_S = 4
MAX_BOXES = 1500
MAX_BOX_TEXT = 1000
BOX_SIDES = ("x", "y", "width", "height")
# The look's sample (#149): read within its own time, and bounded before it is cached.
LOOK_TIMEOUT_S = 2.0
LOOK_KEYS = 40
LOOK_KEY_CHARS = 64
LOOK_FONT_CHARS = 200
# Element ids kept per page, in document order: enough for a one-page site's sections.
MAX_ANCHORS = 200

Refusal = Literal["private", "port", "unreachable"]
# Websites answer on these: any other port is a service, not a site, unless CRAWL_ALLOW_PRIVATE
# (the demo sites run on :8080-:8082 locally). On the team server, the server's own public
# address is "global": without this, a build could read what it serves on other ports (#142).
WEB_PORTS = frozenset({80, 443})

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


# The homepage's computed styles, for its look (#149): only numbers and colours, aggregated in the
# page, never the CSS itself. `builder.look` turns them into a `Look`. Fonts and images stay
# unloaded (`SKIPPED_RESOURCES`): a font's declared family is read, never its file.
_LOOK = """() => {
  const LIMIT = 5000;
  const visible = (cs) => cs.display !== "none" && cs.visibility !== "hidden" && +cs.opacity > 0;
  const area = (r) => Math.max(0, r.width) * Math.max(0, r.height);
  const clear = (c) =>
    !c || c === "none" || c === "transparent" || /rgba\\([^)]*,\\s*0\\)$/.test(c);
  const bump = (map, key, n) => { map[key] = (map[key] || 0) + n; };
  const body = document.body;
  if (!body) return null;
  const bcs = getComputedStyle(body), hcs = getComputedStyle(document.documentElement);
  const look = {
    bg: clear(bcs.backgroundColor) ? hcs.backgroundColor : bcs.backgroundColor,
    ink: bcs.color, body_font: bcs.fontFamily, heading: null,
    backgrounds: {}, texts: {}, borders: {}, radii: [], blob_area: 0, textures: 0,
    page_area: area(document.documentElement.getBoundingClientRect()) || 1,
    page_width: document.documentElement.clientWidth || 1,
  };
  // The restaurant's name: the largest text of the header, the brand or the first headings.
  for (const el of document.querySelectorAll("header *, h1, h2, [class*=brand], [class*=logo]")) {
    const cs = getComputedStyle(el);
    if (!visible(cs) || !el.textContent.trim()) continue;
    const size = parseFloat(cs.fontSize);
    if (!look.heading || size > look.heading.size) {
      look.heading = { size, color: cs.color, font: cs.fontFamily };
    }
  }
  const behind = (el) => {
    for (let e = el; e; e = e.parentElement) {
      const c = getComputedStyle(e).backgroundColor;
      if (!clear(c)) return c;
    }
    return look.bg;
  };
  let seen = 0;
  for (const el of body.querySelectorAll("*")) {
    if (++seen > LIMIT) break;
    const cs = getComputedStyle(el);
    if (!visible(cs)) continue;
    const r = el.getBoundingClientRect(), a = area(r);
    if (/feTurbulence/.test(cs.backgroundImage) && a > 10000) look.textures += 1;
    if (el instanceof SVGElement) {
      const shape = ["path", "rect", "circle", "ellipse", "polygon"].includes(el.tagName);
      if (shape && !clear(cs.fill)) {
        bump(look.backgrounds, cs.fill, a);
        if (el.tagName === "path") look.blob_area += a;
      }
      continue;
    }
    if (!clear(cs.backgroundColor)) bump(look.backgrounds, cs.backgroundColor, a);
    for (const side of ["Top", "Right", "Bottom", "Left"]) {
      const drawn = cs["border" + side + "Style"] !== "none";
      if (parseFloat(cs["border" + side + "Width"]) > 0 && drawn) {
        bump(look.borders, cs["border" + side + "Color"], 1);
      }
    }
    const own = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent.trim());
    const chars = own.join("").length;
    if (chars) bump(look.texts, cs.color + "|" + behind(el), chars);
    const boxed = !clear(cs.backgroundColor) || parseFloat(cs.borderTopWidth) > 0;
    if (boxed && a > 0 && look.radii.length < 500) {
      look.radii.push([parseFloat(cs.borderTopLeftRadius) || 0, r.width, r.height]);
    }
  }
  return look;
}"""


# What Studio's public tab needs (#151): each element's text and box on the page, to find a
# fact's quote on the screenshot. Only text and numbers leave the page.
_BOXES = """(limit) => {
  const boxes = [];
  const body = document.body;
  if (!body) return null;
  for (const el of body.querySelectorAll("*")) {
    if (boxes.length >= limit) break;
    const text = (el.innerText || "").trim();
    if (!text || text.length > 1000) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    boxes.push({ text, x: Math.round(r.left + scrollX), y: Math.round(r.top + scrollY),
                 width: Math.round(r.width), height: Math.round(r.height) });
  }
  return { boxes, height: Math.max(document.documentElement.scrollHeight, body.scrollHeight) };
}"""


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    text: str  # normalized visible text, truncated
    json_ld: list[dict[str, Any]]  # schema.org blocks found on the page
    description: str | None = None  # meta[name=description]
    # Element ids, in document order: where a link like `/#reserver` can land
    anchors: list[str] = field(default_factory=list)
    # The homepage's computed styles (`_LOOK`), for `builder.look`; None on other pages (#149).
    look: dict[str, Any] | None = None
    # The page as seen at `SHOT_WIDTH`, base64 JPEG; None if it couldn't be taken (#151).
    shot: str | None = None
    shot_width: int = 0
    shot_height: int = 0
    # {text, x, y, width, height} in the screenshot's pixels, to locate a fact's quote.
    boxes: list[dict[str, Any]] = field(default_factory=list)


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
        try:
            port = parts.port
        except ValueError:
            return "unreachable"
        if host not in self._verdicts:
            self._verdicts[host] = await self._resolve(host)
        verdict = self._verdicts[host]
        # The DNS verdict is per host; the port is checked on every URL.
        if verdict is None and port is not None and port not in WEB_PORTS:
            return None if self.allow_private else "port"
        return verdict

    async def allows(self, url: str) -> bool:
        """Whether `url` may be loaded; a private address or another port is logged."""
        refusal = await self.refusal(url)
        if refusal in ("private", "port"):
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
    await on_event(_finished("done", pages_read(result.pages), started))
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
        case "port":
            raise InvalidInput(
                f"I can't read {website}: websites answer on the usual ports (80 and 443)."
            )
        case "unreachable":
            raise InvalidInput(f"I couldn't open {website} — is the address right?")
    async with async_playwright() as playwright, await playwright.chromium.launch() as browser:
        # Requests from service workers would bypass the routes below.
        context = await browser.new_context(service_workers="block")
        await context.route("**/*", partial(_route, guard, timeout_s * 1000))
        await context.route_web_socket("**/*", _refuse_web_socket)
        try:
            home, links = await _read(
                context, guard, website, timeout_s=timeout_s, max_chars=max_chars, look=True
            )
        except (PlaywrightError, TimeoutError, _Unreadable) as error:
            if isinstance(error, _Blocked) or (
                isinstance(error, PlaywrightError) and "ERR_BLOCKED_BY_CLIENT" in error.message
            ):
                raise InvalidInput(
                    f"I can't read {website}: it redirects to a private network address "
                    "or another port."
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
    context: BrowserContext,
    guard: HostGuard,
    url: str,
    *,
    timeout_s: float,
    max_chars: int,
    look: bool = False,
) -> tuple[Page, list[list[str]]]:
    """Load `url` in a new tab within `timeout_s`: the page and its links as (href, text), and
    with `look`, its computed styles."""
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
        # Outside the page's deadline, with its own: a heavy page loses its look, not its crawl.
        styles = await _styles(tab) if look else None
        seen = await _capture(tab)
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
        look=styles,
        **seen,
    )
    return page, raw["links"]


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def checked_boxes(read: Any) -> tuple[int, list[dict[str, Any]]] | None:
    """The page's height and text boxes, or None. `_BOXES` runs in the page's own world, whose
    scripts can replace what it calls, so nothing it returns is trusted."""
    if not isinstance(read, dict) or not _number(read.get("height")) or read["height"] <= 0:
        return None
    raw = read.get("boxes")
    if not isinstance(raw, list):
        return None
    boxes = []
    for box in raw[:MAX_BOXES]:
        if not isinstance(box, dict) or not isinstance(box.get("text"), str):
            continue
        sides = [box.get(side) for side in BOX_SIDES]
        if all(_number(side) for side in sides):
            numbers = {side: round(box[side]) for side in BOX_SIDES}
            boxes.append({"text": box["text"][:MAX_BOX_TEXT], **numbers})
    return round(read["height"]), boxes


async def _capture(tab: Any) -> dict[str, Any]:
    """The page's screenshot and text boxes at `SHOT_WIDTH`, for `Page`; empty if they can't be
    had in `SHOT_TIMEOUT_S`, or the image is too heavy: the build goes on without them."""
    try:
        async with asyncio.timeout(SHOT_TIMEOUT_S):
            await tab.set_viewport_size({"width": SHOT_WIDTH, "height": 800})
            read = checked_boxes(await tab.evaluate(_BOXES, MAX_BOXES))
            if read is None:
                log.warning("crawl_boxes_unreadable")
                return {}
            page_height, boxes = read
            height = min(page_height, SHOT_MAX_HEIGHT)
            image = await tab.screenshot(
                type="jpeg",
                quality=SHOT_QUALITY,
                full_page=True,
                clip={"x": 0, "y": 0, "width": SHOT_WIDTH, "height": height},
                animations="disabled",
            )
    except (PlaywrightError, TimeoutError):
        log.warning("crawl_screenshot_failed")
        return {}
    if len(image) > SHOT_MAX_BYTES:
        log.warning("crawl_screenshot_too_heavy", extra={"bytes": len(image)})
        return {}
    return {
        "shot": base64.b64encode(image).decode(),
        "shot_width": SHOT_WIDTH,
        "shot_height": height,
        "boxes": boxes,
    }


async def _styles(tab: Any) -> dict[str, Any] | None:
    """The page's computed styles, bounded; None if they can't be read within `LOOK_TIMEOUT_S`:
    the build then uses the default look, it never fails for it."""
    try:
        styles = await asyncio.wait_for(tab.evaluate(_LOOK), LOOK_TIMEOUT_S)
    except (PlaywrightError, TimeoutError):
        log.warning("crawl_look_unreadable")
        return None
    return bounded_look(styles) if isinstance(styles, dict) else None


def bounded_look(styles: dict[str, Any]) -> dict[str, Any]:
    """The sample as the cache stores it: the page writes its keys (it can even redefine
    `getComputedStyle`), so each map keeps its `LOOK_KEYS` heaviest entries, keys and strings
    cut to `LOOK_KEY_CHARS`."""

    def cut(value: Any, limit: int = LOOK_KEY_CHARS) -> str:
        return str(value)[:limit]

    def heaviest(counts: Any) -> dict[str, float]:
        if not isinstance(counts, dict):
            return {}
        weights = {}
        for key, weight in counts.items():
            try:
                weights[cut(key, 2 * LOOK_KEY_CHARS)] = float(weight)
            except (TypeError, ValueError):
                continue
        ranked = sorted(weights.items(), key=lambda kv: -kv[1])[:LOOK_KEYS]
        return dict(ranked)

    def number(value: Any) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed == parsed and abs(parsed) != float("inf") else None

    heading = styles.get("heading")
    numbers = ("blob_area", "textures", "page_area", "page_width")
    radii = [
        values
        for item in styles.get("radii") or []
        if isinstance(item, list)
        and len(item) == 3
        and None not in (values := [number(v) for v in item])
    ]
    return {
        "bg": cut(styles.get("bg")),
        "ink": cut(styles.get("ink")),
        "body_font": cut(styles.get("body_font"), LOOK_FONT_CHARS),
        "heading": {
            "size": number(heading.get("size")),
            "color": cut(heading.get("color")),
            "font": cut(heading.get("font"), LOOK_FONT_CHARS),
        }
        if isinstance(heading, dict)
        else None,
        "backgrounds": heaviest(styles.get("backgrounds")),
        "texts": heaviest(styles.get("texts")),
        "borders": heaviest(styles.get("borders")),
        "radii": radii[:500],
        **{name: number(styles.get(name)) for name in numbers},
    }


async def _landing(context: BrowserContext, guard: HostGuard, url: str, timeout_ms: float) -> str:
    """Where `url` lands after its redirects, each hop checked by the guard.

    The tab then loads that address: the page keeps its real URL, which relative links resolve
    from, and the browser never follows a redirect itself (see `_route`).
    """
    for _ in range(MAX_REDIRECTS + 1):
        if not await guard.allows(url):
            raise _Blocked("it redirects to a private network address or another port")
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


def page_path(url: str) -> str:
    """The page's path: `/` for the homepage, as a fact's source names it."""
    return urlsplit(url).path or "/"


def pages_read(pages: list[Page]) -> str:
    """The `crawl` step's closing note: how many pages, and their paths."""
    paths = ", ".join(urlsplit(page.url).path or "/" for page in pages)
    count = len(pages)
    return f"Read {count} page{'s' * (count > 1)}: {paths}"


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
