"""What the builder tells a merchant (#184): times on the merchant's clock."""

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from builder.build import BeingBuilt

SITE = "https://lagraineverte.example/"


def test_a_site_being_built_is_free_again_on_the_merchants_clock() -> None:
    free_at = datetime(2026, 11, 1, 17, 40, tzinfo=UTC)
    # Postgres hands timestamps back in the session's zone, whichever it is.
    from_db = free_at.astimezone(timezone(timedelta(hours=-5)))

    local = str(BeingBuilt("la-graine-verte", SITE, from_db, ZoneInfo("Europe/Zurich")))
    utc = str(BeingBuilt("la-graine-verte", SITE, from_db))

    assert local.endswith("anyone else can build it again after 2026-11-01 18:40.")
    assert utc.endswith("anyone else can build it again after 2026-11-01 17:40 UTC.")
