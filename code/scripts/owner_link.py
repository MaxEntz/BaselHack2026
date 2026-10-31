"""A new secret link for a merchant's owner (#132): `owner_link.py la-graine-verte --name Léa`.

For merchants built before accounts existed (seeded demo merchants, the server's), or when an
owner lost their link: the previous link stops working. Prints the link once; only its hash is
stored. Run from `code/`, against `DATABASE_URL`.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence

import seed
from sqlalchemy.exc import DBAPIError

from core.config import get_settings
from core.db import make_engine
from merchant_platform.accounts import (
    Accounts,
    PgOwnerStore,
    Signer,
    login_url,
)
from merchant_platform.overview import registry_records


async def issue(slug: str, name: str | None) -> str | None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        if await registry_records(engine)(slug) is None:
            return None
        # Signing plays no part in issuing a link: any key will do here.
        accounts = Accounts(PgOwnerStore(engine), Signer(b"unused"))
        return login_url(settings.platform_web_url, await accounts.issue_link_token(slug, name))
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print a new secret link for a merchant.")
    parser.add_argument("slug")
    parser.add_argument("--name", help="what the owner asked to be called")
    args = parser.parse_args(argv)
    try:
        link = asyncio.run(issue(args.slug, args.name))
    except DBAPIError as error:
        return seed.database_error(error)
    if link is None:
        print(f"{args.slug}: no such merchant", file=sys.stderr)
        return 1
    print(link)
    return 0


if __name__ == "__main__":
    sys.exit(main())
