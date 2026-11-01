"""Playwright crawl of the merchant website (homepage + relevant same-domain pages).

Every request the browser makes goes through `guard.py` (SSRF prevention); the JavaScript it
runs in the pages is `probes.py`'s, the address helpers `urls.py`'s. This module reads the
pages, captures their screenshot and text boxes for Studio (#151) and the homepage's look
(#149), and reports the `crawl` step.
"""

import asyncio
import base64
import json
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Literal

from playwright.async_api import BrowserContext, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from builder import probes
from builder.guard import BROWSER_ARGS, Blocked, HostGuard, Unreadable, guard_context, landing
from builder.urls import normalize_url, page_name, page_path, useful_links
from core.config import get_settings
from core.errors import InvalidInput
from core.models import StepEvent

log = logging.getLogger("builder.crawl")

StepEmitter = Callable[[StepEvent], Awaitable[None]]

# Longest wait for `networkidle` after `domcontentloaded`: some sites are never idle.
SETTLE_S = 2.0
# Each page's screenshot, for Studio's public tab (#151): the frame's width, bounded so that
# it fits in a chat stream and in a row. Without images and fonts (`guard.SKIPPED_RESOURCES`).
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


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    text: str  # normalized visible text, truncated
    json_ld: list[dict[str, Any]]  # schema.org blocks found on the page
    description: str | None = None  # meta[name=description]
    # Element ids, in document order: where a link like `/#reserver` can land
    anchors: list[str] = field(default_factory=list)
    # The homepage's computed styles (`probes.LOOK`), for `builder.look`; None on other pages.
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


# The crawl


async def crawl(
    url: str,
    on_event: StepEmitter,
    *,
    max_pages: int = 8,
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
    async with (
        async_playwright() as playwright,
        await playwright.chromium.launch(args=BROWSER_ARGS) as browser,
    ):
        # Requests from service workers would bypass the routes below.
        context = await browser.new_context(service_workers="block")
        await guard_context(context, guard, timeout_s)
        try:
            home, links = await _read(
                context, guard, website, timeout_s=timeout_s, max_chars=max_chars, look=True
            )
        except (PlaywrightError, TimeoutError, Unreadable) as error:
            if isinstance(error, Blocked) or (
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
    except (PlaywrightError, TimeoutError, Unreadable) as error:
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
            url = await landing(context, guard, url, timeout_s * 1000)
            response = await tab.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
            if response is not None and not response.ok:
                raise Unreadable(f"error {response.status}")
            # Let scripts fill the page, keeping a second of the budget to read it.
            settle_s = min(SETTLE_S, deadline - loop.time() - 1)
            if settle_s > 0:
                with suppress(PlaywrightTimeout):
                    await tab.wait_for_load_state("networkidle", timeout=settle_s * 1000)
            raw = await tab.evaluate(probes.EXTRACT)
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
    """The page's height and text boxes, or None. `probes.BOXES` runs in the page's own world, whose
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
            read = checked_boxes(await tab.evaluate(probes.BOXES, MAX_BOXES))
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
        styles = await asyncio.wait_for(tab.evaluate(probes.LOOK), LOOK_TIMEOUT_S)
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


def pages_read(pages: list[Page]) -> str:
    """The `crawl` step's closing note: how many pages, and their paths."""
    paths = ", ".join(page_path(page.url) for page in pages)
    count = len(pages)
    return f"Read {count} page{'s' * (count > 1)}: {paths}"


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
