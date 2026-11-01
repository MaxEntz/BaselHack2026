"""What the crawler's browser may connect to (OWASP SSRF prevention).

The crawler loads an address the merchant typed, so every request the browser makes is checked
after DNS resolution: pages, redirect hops, scripts, fetches, frames. Only public addresses
pass, on ports 80 and 443, plus private ones and other ports with `CRAWL_ALLOW_PRIVATE=1` (the
demo sites run locally). Link-local addresses, such as the cloud metadata endpoint
169.254.169.254, never do. Known limit: Chromium resolves the host again after the check, so
DNS rebinding is not covered.
"""

import asyncio
import ipaddress
import logging
import socket
from contextlib import suppress
from functools import partial
from typing import Literal
from urllib.parse import urljoin, urlsplit

from playwright.async_api import BrowserContext, Route, WebSocketRoute
from playwright.async_api import Error as PlaywrightError

# The crawl's logger: the guard's lines kept their name when it moved here.
log = logging.getLogger("builder.crawl")

Refusal = Literal["private", "port", "unreachable"]
# Websites answer on these: any other port is a service, not a site, unless CRAWL_ALLOW_PRIVATE
# (the demo sites run on :8080-:8082 locally). On the team server, the server's own public
# address is "global": without this, a build could read what it serves on other ports (#142).
WEB_PORTS = frozenset({80, 443})
MAX_REDIRECTS = 5
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
# Only the text is read: skip what is heavy and never holds any.
SKIPPED_RESOURCES = frozenset({"image", "media", "font"})


class Unreadable(Exception):
    """The page can't be read: an error status, too many redirects…"""


class Blocked(Unreadable):
    """A redirect leads to an address the guard refuses."""


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


async def guard_context(context: BrowserContext, guard: HostGuard, timeout_s: float) -> None:
    """Route every request of `context` through the guard, and refuse its WebSockets."""
    await context.route("**/*", partial(route_request, guard, timeout_s * 1000))
    await context.route_web_socket("**/*", _refuse_web_socket)


async def route_request(guard: HostGuard, timeout_ms: float, route: Route) -> None:
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
    """Never connected: `route_request` doesn't see WebSockets, and the text never comes from
    them."""
    await web_socket.close()


async def landing(context: BrowserContext, guard: HostGuard, url: str, timeout_ms: float) -> str:
    """Where `url` lands after its redirects, each hop checked by the guard.

    The tab then loads that address: the page keeps its real URL, which relative links resolve
    from, and the browser never follows a redirect itself (see `route_request`).
    """
    for _ in range(MAX_REDIRECTS + 1):
        if not await guard.allows(url):
            raise Blocked("it redirects to a private network address or another port")
        response = await context.request.get(url, max_redirects=0, timeout=timeout_ms)
        location = response.headers.get("location")
        await response.dispose()
        if response.status not in REDIRECT_STATUSES or not location:
            return url
        url = urljoin(url, location)
    raise Unreadable("too many redirects")
