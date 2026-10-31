"""Print the `GATEWAY_TOKEN=…` line Caddy's drop-in reads (ADR-0019). Run from `code/`.

`make deploy` writes it to `/opt/merchant-agents/caddy.env`. The value is the one the gateway's
own `Settings` load, not the raw line of `.env`: python-dotenv drops an inline comment that
systemd's `EnvironmentFile` keeps, so Caddy would send another token, and an empty value turns
the gateway's token check off. Exits non-zero, printing nothing, when the token is unusable.
"""

import sys

from core.config import Settings, get_settings

MISSING = "Set GATEWAY_TOKEN in code/.env on the server first (documentation/runbooks/connector.md)"
UNSAFE = "GATEWAY_TOKEN must have no spaces, quotes or backslashes: systemd would read it otherwise"


def caddy_env(settings: Settings) -> str:
    """The line for `caddy.env`, or `SystemExit` with the reason."""
    token = settings.gateway_token.get_secret_value() if settings.gateway_token else ""
    if not token.strip():
        raise SystemExit(MISSING)
    if any(char.isspace() or char in "\"'\\" for char in token):
        raise SystemExit(UNSAFE)
    return f"GATEWAY_TOKEN={token}"


def main() -> int:
    print(caddy_env(get_settings()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
