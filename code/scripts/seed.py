"""Seed the demo (#10): the 3 merchants of tests/fixtures, and tonight's bookings.

Run from `code/`; the options combine:

    uv run python scripts/seed.py --all                     # the 3 merchants, live
    uv run python scripts/seed.py --except le-petit-zinc    # demo mode: onboarding builds it live
    uv run python scripts/seed.py --with-bookings           # tonight's bookings at Le Petit Zinc
    uv run python scripts/seed.py --with-management         # La Graine Verte's management data

A merchant already in the registry keeps its data and is set live again. Bookings use fixed
idempotency keys: seeding twice on the same day books nothing twice. The management data (#131,
`scripts/demo_management.py`) goes in once, dated from the day of the seed.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import demo_management
from pydantic import HttpUrl
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from agent import provisioning, service
from core.config import get_settings
from core.db import make_engine
from core.errors import AgentError
from core.models import MerchantProfile, Via

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
MERCHANTS = ("le-petit-zinc", "la-graine-verte", "maison-albane")
BOOKINGS_AT = "le-petit-zinc"


@dataclass(frozen=True)
class Booking:
    time: str  # tonight, in MERCHANT_TZ
    party_size: int
    guest_name: str
    via: Via


# "What bookings do I have tonight?" is not empty, tables of 4 stay free at 20:00 for "a table
# for 4 tonight at 8pm", and the 21:00 booking survives "Block 21:00 tonight".
TONIGHT = (
    Booking("19:00", 2, "Camille Muller", "website"),
    # Guests' bookings first: "Cancel R-001" (§9) leaves the guest a notice, which a booking
    # the merchant entered itself doesn't (#134), even when the seed runs after 19:00.
    Booking("19:30", 4, "Thomas Schmitt", "website"),
    Booking("20:00", 6, "Famille Keller", "website"),
    Booking("21:00", 3, "Léa Meyer", "merchant"),
)


def public_address(url: HttpUrl, slug: str, domain: str) -> HttpUrl:
    parts = urlsplit(str(url))
    return HttpUrl(
        urlunsplit(("https", f"{slug}.{domain}", parts.path, parts.query, parts.fragment))
    )


def load_profile(slug: str, sites_domain: str | None = None) -> MerchantProfile:
    profile = MerchantProfile.model_validate_json((FIXTURES / f"{slug}.json").read_text())
    if sites_domain is None:
        return profile
    return profile.model_copy(
        update={
            "website": public_address(profile.website, slug, sites_domain),
            "fallback_url": public_address(profile.fallback_url, slug, sites_domain),
        }
    )


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed the demo merchants and tonight's bookings.")
    merchants = parser.add_mutually_exclusive_group()
    merchants.add_argument("--all", action="store_true", help="the 3 demo merchants")
    merchants.add_argument(
        "--except",
        dest="excluded",
        nargs="+",
        choices=MERCHANTS,
        metavar="SLUG",
        help="every demo merchant but these",
    )
    parser.add_argument(
        "--with-bookings", action="store_true", help=f"tonight's bookings at {BOOKINGS_AT}"
    )
    parser.add_argument(
        "--with-management",
        action="store_true",
        help=f"{demo_management.MERCHANT}'s bookings, as the management site's mockup shows them",
    )
    return parser


def selected(args: argparse.Namespace) -> list[str]:
    if args.all:
        return list(MERCHANTS)
    return [slug for slug in MERCHANTS if args.excluded and slug not in args.excluded]


async def seed_merchant(engine: AsyncEngine, profile: MerchantProfile) -> None:
    try:
        await provisioning.provision(profile, engine=engine)
    except provisioning.SlugTaken as error:  # already in the registry: keep its data
        # Live again whatever its status: `deploy` only publishes a `building` merchant (#220).
        await provisioning.set_status(profile.slug, "live", engine=engine)
        print(f"{profile.slug}: kept ({error}), live")
        return
    await provisioning.deploy(profile.slug, engine=engine)
    print(f"{profile.slug}: provisioned, live")


async def seed_bookings(engine: AsyncEngine, now: datetime) -> int:
    """Book TONIGHT at Le Petit Zinc, skipping the times already past; returns the failures."""
    agent = service.PgAgentService(engine, tz=now.tzinfo)
    failures = 0
    for booking in TONIGHT:
        starts_at = datetime.combine(now.date(), time.fromisoformat(booking.time), now.tzinfo)
        if starts_at <= now:
            print(f"{BOOKINGS_AT} {booking.time}: skipped, already past")
            continue
        try:
            reservation = await agent.create_reservation(
                BOOKINGS_AT,
                starts_at,
                booking.party_size,
                booking.guest_name,
                idempotency_key=f"seed-{BOOKINGS_AT}-{starts_at.isoformat()}",
                via=booking.via,
            )
        except AgentError as error:
            failures += 1
            print(f"{BOOKINGS_AT} {booking.time}: {error.code}: {error}", file=sys.stderr)
            continue
        print(
            f"{BOOKINGS_AT} {booking.time}: {reservation.id}, {reservation.party_size} guests, "
            f"table {reservation.table_label}, via {reservation.via}"
        )
    return failures


async def run(args: argparse.Namespace) -> int:
    engine = make_engine()  # one pool for every call
    try:
        for slug in selected(args):
            await seed_merchant(engine, load_profile(slug, get_settings().demo_sites_domain))
        if not (args.with_bookings or args.with_management):
            return 0
        now = datetime.now(ZoneInfo(get_settings().merchant_tz))
        if args.with_management:
            done = await demo_management.seed_management(engine, now.date())
            print(f"{demo_management.MERCHANT}: {done}")
        if not args.with_bookings:
            return 0
        return 1 if await seed_bookings(engine, now) else 0
    finally:
        await engine.dispose()


def database_error(error: DBAPIError) -> int:
    url = make_url(get_settings().database_url).render_as_string(hide_password=True)
    print(f"Database error on {url}: {error.orig}", file=sys.stderr)
    print("Is Postgres up (`make db`) and migrated (`make migrate`)?", file=sys.stderr)
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not (args.all or args.excluded or args.with_bookings or args.with_management):
        parser().error(
            "nothing to do: pass --all, --except SLUG, --with-bookings or --with-management"
        )
    try:
        return asyncio.run(run(args))
    except AgentError as error:
        print(f"{error.code}: {error}", file=sys.stderr)
        return 1
    except DBAPIError as error:
        return database_error(error)


if __name__ == "__main__":
    sys.exit(main())
