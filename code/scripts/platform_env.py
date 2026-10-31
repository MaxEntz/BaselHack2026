"""Refuse a platform its own settings would leave open (#142, #132). Run from `platform/`.

`make deploy` runs it in `/opt/merchant-agents/platform`, so it loads that `.env` as the
service will. It checks the values `Settings` load, not the raw lines: python-dotenv reads
`PLATFORM_INVITE_CODE=""`, or a value that is only a comment, as unset, and an unset invite
code lets anyone start a build. Exits non-zero with the reason; prints nothing when they hold.
"""

import sys

from pydantic import ValidationError

from core.config import Settings, get_settings
from merchant_platform.api import check_account_settings

STEP = "(documentation/runbooks/platform.md, step 2)"


def check(settings: Settings) -> None:
    """`SystemExit` with the reason when the platform must not run with `settings`."""
    if settings.platform_dev_routes:
        raise SystemExit(f"PLATFORM_DEV_ROUTES must be off on the server {STEP}")
    if not settings.platform_web_url.startswith("https://sesame."):
        raise SystemExit(f"PLATFORM_WEB_URL must be the https sesame. host {STEP}")
    if settings.platform_cookie_secret is None:
        raise SystemExit(
            f"Set PLATFORM_COOKIE_SECRET: owners would be signed out on restart {STEP}"
        )
    if settings.platform_invite_code is None:
        raise SystemExit(f"Set PLATFORM_INVITE_CODE: without it, anyone can start a build {STEP}")
    try:
        check_account_settings(settings)
    except RuntimeError as refused:
        raise SystemExit(f"{refused} {STEP}") from None


def main() -> int:
    try:
        settings = get_settings()
    except ValidationError as invalid:
        first = invalid.errors()[0]
        field = ".".join(map(str, first["loc"])).upper()
        raise SystemExit(f"platform/.env doesn't load, {field}: {first['msg']} {STEP}") from None
    check(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
