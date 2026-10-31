"""Who may read a merchant's data on the platform: its owner, nothing else.

Fail-closed until merchant accounts land (#132): only the development merchant
(PLATFORM_MERCHANT, on `app.state.development_merchant`) is anyone's, every other merchant is
nobody's. Every `/merchants/{slug}` route depends on `owner_of_path`, so an implemented route
can't ship open.
"""

from typing import Annotated

from fastapi import Path, Request

from core.models import Slug

NOT_SIGNED_IN = "Open your private link to manage this restaurant."
OTHER_MERCHANT = "Your link opens another restaurant."


class NotSignedIn(Exception):
    """`401` `not_signed_in`: the request has no owner."""


class OtherMerchant(Exception):
    """`403` `other_merchant`: the request's owner runs another merchant."""


async def request_owner(request: Request) -> str | None:
    """The slug of the merchant the request's owner runs, if any."""
    development: str | None = request.app.state.development_merchant
    return development


async def require_owner(request: Request, slug: str) -> str:
    owner = await request_owner(request)
    if owner is None:
        raise NotSignedIn(NOT_SIGNED_IN)
    if owner != slug:
        raise OtherMerchant(OTHER_MERCHANT)
    return owner


async def owner_of_path(request: Request, slug: Annotated[Slug, Path()]) -> str:
    """Dependency of every `/merchants/{slug}` route."""
    return await require_owner(request, slug)
