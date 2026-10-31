"""Demo outage switch (#10): `set_status.py le-petit-zinc down`, then `live` to restore it.

A `down` merchant stays in the registry: the gateway answers `agent_unavailable` with its
fallback URL (AGENTS.md §8.4). Run from `code/`.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence

import seed
from sqlalchemy.exc import DBAPIError

from agent import provisioning
from core.errors import AgentError


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set a merchant live or down.")
    parser.add_argument("slug")
    parser.add_argument("status", choices=["live", "down"])
    args = parser.parse_args(argv)
    try:
        asyncio.run(provisioning.set_status(args.slug, args.status))
    except AgentError as error:
        print(f"{args.slug}: {error.code}: {error}", file=sys.stderr)
        return 1
    except DBAPIError as error:
        return seed.database_error(error)
    print(f"{args.slug}: {args.status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
