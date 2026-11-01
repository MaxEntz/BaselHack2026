"""Per-address limits on the routes anyone may call (#232): `POST /invite`, `GET /login` and the
showcase site's `POST /public/{slug}/reservations`.

Counted in this process: the platform runs one worker (ADR-0016), so its count for an address
is the whole count. Behind Caddy, uvicorn's `--proxy-headers` makes `request.client` the
visitor's address (`deploy/merchant-platform.service`); Caddy sets `X-Forwarded-For` itself.
"""

import logging
import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from fastapi import Request

log = logging.getLogger("merchant_platform.limits")

LimitedRoute = Literal["invite", "login", "public_booking"]
# Addresses remembered per route: past it, the one seen longest ago is forgotten, so that a
# flood of addresses can't grow memory without bound.
MAX_CLIENTS = 10_000


@dataclass(frozen=True)
class Limit:
    requests: int
    per_seconds: float


LIMITS: dict[LimitedRoute, Limit] = {
    # An invitation code, guessed 10 tries every 10 minutes from one address.
    "invite": Limit(10, 600),
    # A secret link can't be guessed (32 random bytes), but each try costs a database lookup.
    "login": Limit(30, 600),
    # A guest books one table, maybe retries: 10 in 10 minutes leaves room, not an evening's worth.
    "public_booking": Limit(10, 600),
}


class TooManyRequests(Exception):
    """Answered `429 too_many_requests`, with `Retry-After` in whole seconds."""

    def __init__(self, retry_after: float) -> None:
        self.retry_after = max(1, math.ceil(retry_after))
        minutes = math.ceil(self.retry_after / 60)
        super().__init__(
            f"Too many requests from this address. Try again in {minutes} minute"
            f"{'' if minutes == 1 else 's'}."
        )


class RateLimiter:
    """At most `limit.requests` requests per `limit.per_seconds` from one address.

    A refused request isn't counted, so an address that waits gets through again.
    """

    def __init__(self, limit: Limit, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.clock = clock
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def hit(self, client: str) -> float | None:
        """Count one request from `client`: None if it may go on, else seconds to wait."""
        now = self.clock()
        hits = self._hits.pop(client, None) or deque()
        while hits and hits[0] <= now - self.limit.per_seconds:
            hits.popleft()
        self._hits[client] = hits
        if len(self._hits) > MAX_CLIENTS:
            self._hits.popitem(last=False)
        if len(hits) >= self.limit.requests:
            # A limit of 0 closes the route: nothing to wait for but a whole window.
            return hits[0] + self.limit.per_seconds - now if hits else self.limit.per_seconds
        hits.append(now)
        return None


def rate_limiters(clock: Callable[[], float] = time.monotonic) -> dict[LimitedRoute, RateLimiter]:
    """One limiter per limited route, for one app."""
    return {route: RateLimiter(limit, clock) for route, limit in LIMITS.items()}


def limited(route: LimitedRoute) -> Callable[[Request], None]:
    """A route dependency: counts the request against the app's limit for `route`.

    It runs before the body is validated: a try with a body outside the schema counts too.
    """

    def check(request: Request) -> None:
        limiter: RateLimiter = request.app.state.rate_limits[route]
        client = request.client.host if request.client else "unknown"
        if (wait := limiter.hit(client)) is not None:
            # Not the address: a log line of the platform never names a visitor.
            log.warning("rate_limited", extra={"route": route})
            raise TooManyRequests(wait)

    return check
