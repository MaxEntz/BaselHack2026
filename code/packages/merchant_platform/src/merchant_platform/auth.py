"""Who may read a merchant's data on the platform (#132): its owner's cookie, nothing else.

`create_app` puts the `Accounts`, the development merchant (PLATFORM_MERCHANT) and whether
cookies are `Secure` on `app.state`; these dependencies and routes read them from the request.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from core.models import Slug
from merchant_platform.accounts import (
    COOKIE_MAX_AGE,
    INVITE_COOKIE,
    OWNER_COOKIE,
    Accounts,
    Owner,
)

NOT_SIGNED_IN = "Open your private link to manage this restaurant."
OTHER_MERCHANT = "Your link opens another restaurant."


class NotSignedIn(Exception):
    """`401` `not_signed_in`: no valid owner cookie."""


class OtherMerchant(Exception):
    """`403` `other_merchant`: the cookie is another merchant's."""


class NeedsInvite(Exception):
    """`403` `needs_invite`: starting a build needs PLATFORM_INVITE_CODE's invitation."""


def accounts_of(request: Request) -> Accounts:
    accounts: Accounts = request.app.state.accounts
    return accounts


async def cookie_owner(request: Request) -> Owner | None:
    """The owner the request's cookie names, if genuine and current."""
    return await accounts_of(request).owner_from_cookie(request.cookies.get(OWNER_COOKIE))


async def request_owner(request: Request) -> Owner | None:
    """The cookie's owner, else the development merchant (PLATFORM_MERCHANT), else None."""
    owner = await cookie_owner(request)
    if owner is not None:
        return owner
    development: str | None = request.app.state.development_merchant
    return Owner(development, None, "") if development else None


async def require_owner(request: Request, slug: str) -> Owner:
    owner = await request_owner(request)
    if owner is None:
        raise NotSignedIn(NOT_SIGNED_IN)
    if owner.slug != slug:
        raise OtherMerchant(OTHER_MERCHANT)
    return owner


async def owner_of_path(request: Request, slug: Annotated[Slug, Path()]) -> Owner:
    """Dependency of every `/merchants/{slug}` route."""
    return await require_owner(request, slug)


# `GET /login`, `POST /logout`, `POST /invite`

router = APIRouter(tags=["accounts"])

BAD_LINK = "This link doesn't open any restaurant. Ask for a new one."
BAD_INVITE = "That invitation code isn't valid."
log = logging.getLogger("merchant_platform.auth")


class InviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: Annotated[str, Field(max_length=200)]


def _set_cookie(request: Request, response: Response, name: str, value: str) -> None:
    secure: bool = request.app.state.secure_cookies
    response.set_cookie(
        name,
        value,
        max_age=int(COOKIE_MAX_AGE.total_seconds()),
        path="/",
        httponly=True,
        secure=secure,
        samesite="lax",
    )


@router.get("/login")
async def login(request: Request, token: Annotated[str, Query(max_length=200)]) -> RedirectResponse:
    """The secret link: opens the merchant's management site in this browser."""
    owner = await accounts_of(request).owner_for_token(token)
    log.info("login", extra={"merchant": owner.slug if owner else None, "ok": bool(owner)})
    if owner is None:
        raise NotSignedIn(BAD_LINK)
    response = RedirectResponse(f"/manage/{owner.slug}", status_code=303)
    _set_cookie(request, response, OWNER_COOKIE, accounts_of(request).owner_cookie(owner))
    return response


@router.post("/logout", status_code=204)
async def logout() -> Response:
    response = Response(status_code=204)
    response.delete_cookie(OWNER_COOKIE, path="/")
    return response


@router.post("/invite", status_code=204)
async def invite(request: Request, body: InviteRequest) -> Response:
    """Lets this browser start a build when PLATFORM_INVITE_CODE is set."""
    accounts = accounts_of(request)
    response = Response(status_code=204)
    if not accounts.needs_invite:
        return response
    if not accounts.accepts_invite(body.code):
        raise NeedsInvite(BAD_INVITE)
    _set_cookie(request, response, INVITE_COOKIE, accounts.invite_cookie())
    return response
