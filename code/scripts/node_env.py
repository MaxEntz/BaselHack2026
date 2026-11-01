"""Refuse a BATP node that couldn't run the merchant's agent (#250). Run from `code/`.

`make deploy` runs it in `/opt/merchant-agents/code`, so it loads that `.env` as the service
will. It checks the value `Settings` load, not the raw line: python-dotenv reads
`OPENAI_API_KEY=""`, or a value that is only a comment, as unset. Exits non-zero with the
reason; prints nothing when it holds, and never the key.
"""

import sys

from core.config import Settings, get_settings

MISSING = (
    "Set OPENAI_API_KEY in code/.env on the server: the merchant's agent needs it, and the "
    "platform's file is out of the node's reach (documentation/runbooks/connector.md)"
)


def check(settings: Settings) -> None:
    """`SystemExit` with the reason when the node would have no key."""
    key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
    if not key.strip():
        raise SystemExit(MISSING)


def main() -> int:
    check(get_settings())
    return 0


if __name__ == "__main__":
    sys.exit(main())
