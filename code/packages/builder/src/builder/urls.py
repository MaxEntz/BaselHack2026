"""The website's addresses as the crawl and the extraction compare them: pure functions."""

import re
import unicodedata
from collections.abc import Sequence
from urllib.parse import unquote, urlsplit, urlunsplit

from core.errors import InvalidInput

# AGENTS.md §8.3: the secondary pages worth reading, matched on a link's path or text, in
# English, French and German (Speisekarte, Öffnungszeiten, Kontakt: most Basel sites).
USEFUL_LINK = re.compile(
    r"menu|carte|karte|hours|horaires|zeiten|contact|kontakt|book|reserv"
    r"|about|a[- ]propos|histoire|ueber|uber uns|acces|anfahrt|faq|event|evenement",
    re.IGNORECASE,
)
# A port is the default one only for its own scheme: `http://x:443/` is not `http://x/`.
DEFAULT_PORTS = {"http": 80, "https": 443}

# Host without `www.`, and the port unless it is its scheme's default.
Site = tuple[str, int | None]
# A page: its site, its path without a trailing slash, its query.
PageKey = tuple[Site, str, str]


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


def site_of(url: str) -> Site:
    """`http://x`, `https://www.x` and `https://x:443` are one site, `http://x:443` another;
    `ValueError` for a port that doesn't parse."""
    parts = urlsplit(url)
    port = parts.port
    default = DEFAULT_PORTS.get(parts.scheme.lower())
    return (parts.hostname or "").removeprefix("www."), None if port == default else port


def same_site(url: str, website: str) -> bool:
    """Whether `url` is on the merchant's site; `ValueError` for a port that doesn't parse."""
    site = site_of(url)
    return bool(site[0]) and site == site_of(website)


def page_key(url: str) -> PageKey:
    """What tells two pages apart: `/contact`, `/contact/` and `https://www.…/contact#map` are
    one page. `ValueError` for a port that doesn't parse."""
    parts = urlsplit(url)
    return site_of(url), parts.path.rstrip("/"), parts.query


def useful_links(links: Sequence[Sequence[str]], home: str, max_pages: int) -> list[str]:
    """Links to pages of the same site whose path or text names a useful page, in order,
    without fragment or duplicates, `max_pages` at most."""
    seen = {page_key(home)}
    found: list[str] = []
    for href, text in links:
        if len(found) >= max_pages:
            break
        parts = urlsplit(href)
        if parts.scheme not in ("http", "https") or not same_site(href, home):
            continue
        if page_key(href) in seen:
            continue
        if USEFUL_LINK.search(_fold(unquote(parts.path))) or USEFUL_LINK.search(_fold(text)):
            seen.add(page_key(href))
            found.append(urlunsplit(parts._replace(fragment="")))
    return found


def page_path(url: str) -> str:
    """The page's path: `/` for the homepage, as a fact's source names it."""
    return urlsplit(url).path or "/"


def page_name(url: str) -> str:
    """Short name of a page for the chat: its last path segment, without extension."""
    parts = urlsplit(url)
    segments = [segment for segment in unquote(parts.path).split("/") if segment]
    if not segments:
        return parts.query or parts.netloc
    return re.sub(r"\.\w{2,5}$", "", segments[-1])


def _fold(text: str) -> str:
    """Without accents, so that "Réserver" matches `reserv`."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
