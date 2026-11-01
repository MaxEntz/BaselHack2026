"""The settings the merchants' accounts start from (#132): checked before the platform starts.

Kept apart from the API so that `scripts/platform_env.py` can check a deployment's `.env`
without importing FastAPI, OpenAI or Playwright.
"""

import logging
import secrets

from core.config import Settings
from merchant_platform.accounts import Accounts, MemoryOwnerStore, PgOwnerStore, Signer

# The platform API's logger: these lines kept their name when they moved here.
log = logging.getLogger("merchant_platform.api")

# Long enough not to be guessed online (POST /invite has no throttle) or offline (the key).
MIN_INVITE_CODE = 8
MIN_COOKIE_SECRET = 32
DEVELOPMENT_ONLY = (
    "{name} is for local development only: {fix}, or unset PLATFORM_COOKIE_SECRET, "
    "PLATFORM_INVITE_CODE and the https PLATFORM_WEB_URL"
)


def check_account_settings(settings: Settings) -> None:
    """Refuse a platform that would open guest data or take a weak secret (#132).

    PLATFORM_MERCHANT opens its merchant to anyone, and PLATFORM_DEV_ROUTES an unauthenticated
    `/dev/reset-session`: both are for local development, so neither can come with what only a
    deployment sets (an https web app, a cookie key, an invite code). `make deploy` refuses
    them too (`scripts/platform_env.py`); this check holds wherever the platform starts (#220).
    """
    secret = settings.platform_cookie_secret
    invite = settings.platform_invite_code
    https = settings.platform_web_url.startswith("https://")
    deployed = https or secret or invite
    if settings.platform_merchant and deployed:
        raise RuntimeError(DEVELOPMENT_ONLY.format(name="PLATFORM_MERCHANT", fix="unset it"))
    if settings.platform_dev_routes and deployed:
        raise RuntimeError(DEVELOPMENT_ONLY.format(name="PLATFORM_DEV_ROUTES", fix="set it to 0"))
    if secret is not None and len(secret.get_secret_value()) < MIN_COOKIE_SECRET:
        raise RuntimeError(f"PLATFORM_COOKIE_SECRET needs at least {MIN_COOKIE_SECRET} characters")
    if invite is not None and len(invite.get_secret_value()) < MIN_INVITE_CODE:
        raise RuntimeError(f"PLATFORM_INVITE_CODE needs at least {MIN_INVITE_CODE} characters")
    if secret is not None and not https:
        log.warning("PLATFORM_WEB_URL is not https: owner cookies are sent without Secure")


def default_accounts(settings: Settings, store: MemoryOwnerStore | PgOwnerStore) -> Accounts:
    """The owners' links and cookies, signed with PLATFORM_COOKIE_SECRET (a key of this process
    when unset) and gated by PLATFORM_INVITE_CODE."""
    if settings.platform_cookie_secret is None:
        secret = secrets.token_bytes(32)
        log.warning("PLATFORM_COOKIE_SECRET unset: owner cookies end with this process")
    else:
        secret = settings.platform_cookie_secret.get_secret_value().encode()
    invite = settings.platform_invite_code
    return Accounts(store, Signer(secret), invite.get_secret_value() if invite else None)
