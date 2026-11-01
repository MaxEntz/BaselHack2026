"""Seed, demo reset and outage switch (#10), with provisioning (#7) and the service (#8) faked."""

from collections.abc import Iterator
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import demo_booking
import demo_management
import demo_reset
import pytest
import seed
import set_status

from agent import provisioning, service
from core.config import get_settings
from core.errors import InvalidInput, NotFound
from core.models import MerchantProfile, Reservation, Via

NO_ENGINE: Any = object()
ZURICH = ZoneInfo("Europe/Zurich")


class SlugTaken(InvalidInput):
    """`agent.provisioning.SlugTaken` (#36)."""


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class FakeRegistry:
    """`agent.provisioning` as #36 implements it: the engine is an optional keyword."""

    def __init__(self) -> None:
        self.engine = FakeEngine()
        self.engines_used: set[object] = set()
        self.statuses: dict[str, str] = {}
        self.provisioned: list[str] = []
        self.profiles: dict[str, MerchantProfile] = {}
        self.invalid: set[str] = set()

    async def provision(self, profile: MerchantProfile, *, engine: object = None) -> None:
        self.engines_used.add(engine)
        if profile.slug in self.invalid:
            raise InvalidInput(f"Invalid profile for {profile.slug}")
        if profile.slug in self.statuses:
            raise SlugTaken(f"Merchant {profile.slug!r} is already provisioned")
        self.provisioned.append(profile.slug)
        self.profiles[profile.slug] = profile
        self.statuses[profile.slug] = "building"

    async def deploy(self, slug: str, *, engine: object = None) -> None:
        # Only from `building`, as the real one (#220).
        self.engines_used.add(engine)
        if slug not in self.statuses:
            raise NotFound(f"Unknown merchant: {slug!r}")
        if self.statuses[slug] != "building":
            raise provisioning.NotBuilding(slug, self.statuses[slug])  # type: ignore[arg-type]
        self.statuses[slug] = "live"

    async def set_status(self, slug: str, status: str, *, engine: object = None) -> None:
        self.engines_used.add(engine)
        if slug not in self.statuses:
            raise NotFound(f"Unknown merchant: {slug!r}")
        self.statuses[slug] = status


class FakeAgent:
    """`create_reservation` of the AgentService protocol, idempotent like #8's."""

    def __init__(self) -> None:
        self.reservations: dict[str, Reservation] = {}
        self.missing = False
        self.engines: list[object] = []

    async def create_reservation(
        self,
        slug: str,
        starts_at: datetime,
        party_size: int,
        guest_name: str,
        idempotency_key: str,
        via: Via,
    ) -> Reservation:
        if self.missing:
            raise NotFound(f"Unknown merchant {slug}")
        if idempotency_key not in self.reservations:
            self.reservations[idempotency_key] = Reservation(
                id=f"R-{len(self.reservations) + 1:03d}",
                merchant=slug,
                starts_at=starts_at,
                party_size=party_size,
                table_label="T1",
                status="confirmed",
                guest_name=guest_name,
                via=via,
            )
        return self.reservations[idempotency_key]


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRegistry]:
    fake = FakeRegistry()
    for name in ("provision", "deploy", "set_status"):
        monkeypatch.setattr(provisioning, name, getattr(fake, name), raising=False)
    monkeypatch.setattr(provisioning, "SlugTaken", SlugTaken, raising=False)
    monkeypatch.setattr(seed, "make_engine", lambda: fake.engine)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.delenv("DEMO_SITES_DOMAIN", raising=False)
    get_settings.cache_clear()
    yield fake
    get_settings.cache_clear()


@pytest.fixture
def agent(monkeypatch: pytest.MonkeyPatch) -> FakeAgent:
    fake = FakeAgent()

    def pg_agent_service(engine: object, tz: object = None) -> FakeAgent:  # as in #42
        fake.engines.append(engine)
        return fake

    monkeypatch.setattr(service, "PgAgentService", pg_agent_service)
    return fake


def test_demo_mode_seeds_every_merchant_but_le_petit_zinc(registry: FakeRegistry) -> None:
    assert seed.main(["--except", "le-petit-zinc"]) == 0

    assert registry.provisioned == ["la-graine-verte", "maison-albane"]
    assert registry.statuses == {"la-graine-verte": "live", "maison-albane": "live"}
    assert registry.engines_used == {registry.engine}
    assert registry.engine.disposed


def test_what_the_agent_knows_points_to_the_public_sites_with_a_sites_domain() -> None:
    profile = seed.load_profile("le-petit-zinc", "sites.example")

    assert profile.knowledge is not None
    urls = {str(item.source_url) for item in profile.knowledge.items}
    assert "https://le-petit-zinc.sites.example/carte.html" in urls
    assert not [url for url in urls if "localhost" in url]


def test_seeded_links_stay_local_without_a_sites_domain(registry: FakeRegistry) -> None:
    assert seed.main(["--except", "le-petit-zinc"]) == 0

    profile = registry.profiles["la-graine-verte"]
    assert str(profile.website) == "http://localhost:8081/"
    assert str(profile.fallback_url) == "http://localhost:8081/#reserver"


def test_seeded_links_open_the_public_sites_with_a_sites_domain(
    registry: FakeRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEMO_SITES_DOMAIN", "sites.example")
    get_settings.cache_clear()

    assert seed.main(["--all"]) == 0

    assert {
        slug: (str(profile.website), str(profile.fallback_url))
        for slug, profile in registry.profiles.items()
    } == {
        "le-petit-zinc": (
            "https://le-petit-zinc.sites.example/",
            "https://le-petit-zinc.sites.example/reservation.html",
        ),
        "la-graine-verte": (
            "https://la-graine-verte.sites.example/",
            "https://la-graine-verte.sites.example/#reserver",
        ),
        "maison-albane": (
            "https://maison-albane.sites.example/",
            "https://maison-albane.sites.example/#reserver",
        ),
    }


def test_seeding_again_keeps_existing_merchants_and_sets_them_live(
    registry: FakeRegistry,
) -> None:
    registry.statuses["le-petit-zinc"] = "down"

    assert seed.main(["--all"]) == 0

    assert registry.provisioned == ["la-graine-verte", "maison-albane"]
    assert set(registry.statuses.values()) == {"live"}


def test_a_provisioning_error_is_reported_not_taken_for_an_existing_merchant(
    registry: FakeRegistry, capsys: pytest.CaptureFixture[str]
) -> None:
    registry.invalid.add("maison-albane")

    assert seed.main(["--all"]) == 1

    assert "maison-albane" not in registry.statuses
    assert "invalid_input" in capsys.readouterr().err
    assert registry.engine.disposed


@pytest.mark.parametrize("argv", [[], ["--except", "chez-mimi"], ["--all", "--except", "x"]])
def test_seed_refuses_bad_options(registry: FakeRegistry, argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        seed.main(argv)

    assert exit_info.value.code == 2
    assert registry.provisioned == []


async def test_tonight_bookings_skip_past_times_and_never_book_twice(agent: FakeAgent) -> None:
    now = datetime(2026, 10, 31, 19, 15, tzinfo=ZURICH)

    assert await seed.seed_bookings(NO_ENGINE, now) == 0
    assert await seed.seed_bookings(NO_ENGINE, now) == 0

    booked = sorted(agent.reservations.values(), key=lambda r: r.starts_at)
    assert [(r.starts_at.isoformat(), r.party_size, r.via) for r in booked] == [
        ("2026-10-31T19:30:00+01:00", 4, "website"),
        ("2026-10-31T20:00:00+01:00", 6, "website"),
        ("2026-10-31T21:00:00+01:00", 3, "merchant"),
    ]
    assert {r.merchant for r in booked} == {"le-petit-zinc"}


async def test_bookings_without_le_petit_zinc_are_reported(agent: FakeAgent) -> None:
    agent.missing = True

    assert await seed.seed_bookings(NO_ENGINE, datetime(2026, 10, 31, 12, 0, tzinfo=ZURICH)) == 4


def test_bookings_share_the_provisioning_engine(
    registry: FakeRegistry, agent: FakeAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    get_settings.cache_clear()
    try:
        assert seed.main(["--all", "--with-bookings"]) == 0
    finally:
        get_settings.cache_clear()

    assert agent.engines == [registry.engine]
    assert registry.engine.disposed


def test_management_data_goes_in_after_the_merchants_on_their_engine(
    registry: FakeRegistry, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[object, list[str]]] = []

    async def seed_management(engine: object, today: object) -> str:
        calls.append((engine, list(registry.statuses)))
        if len(calls) > 1:
            raise NotFound("la-graine-verte isn't in the registry: seed it too (`--all`)")
        return "management data: …"

    monkeypatch.setattr(demo_management, "seed_management", seed_management)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    get_settings.cache_clear()
    try:
        assert seed.main(demo_reset.DEMO_MODE) == 0
        assert calls == [(registry.engine, ["la-graine-verte", "maison-albane"])]
        assert "la-graine-verte: management data" in capsys.readouterr().out
        assert seed.main(["--with-management"]) == 1
        assert "not_found" in capsys.readouterr().err
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(
    ("url", "local"),
    [
        ("postgresql+psycopg://merchant:merchant@localhost:5432/merchant_agents", True),
        ("postgresql+psycopg://merchant:merchant@127.0.0.1:5433/merchant_agents", True),
        ("postgresql+psycopg://merchant:merchant@[::1]/merchant_agents", True),
        ("postgresql+psycopg://merchant:merchant@191.96.88.152/merchant_agents", False),
        ("postgresql+psycopg://merchant:merchant@localhost.example.com/db", False),
        ("postgresql+psycopg://merchant:merchant@localhost/db?host=db.example.com", False),
        ("postgresql+psycopg://merchant:merchant@/db?host=/var/run/postgresql", False),
    ],
)
def test_demo_reset_only_accepts_localhost(url: str, local: bool) -> None:
    assert demo_reset.is_local(url) is local


def test_demo_reset_refuses_a_remote_database_before_touching_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    remote = "postgresql+psycopg://merchant:s3cret@191.96.88.152:5432/merchant_agents"
    monkeypatch.setattr(demo_reset, "get_settings", lambda: SimpleNamespace(database_url=remote))
    monkeypatch.setattr(demo_reset.command, "upgrade", lambda *_: pytest.fail("migrations ran"))

    assert demo_reset.main([]) == 2

    error = capsys.readouterr().err
    assert "191.96.88.152" in error
    assert "s3cret" not in error


def test_demo_reset_checks_its_options_before_touching_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(demo_reset, "get_settings", lambda: pytest.fail("settings read"))

    with pytest.raises(SystemExit):
        demo_reset.main(["--except", "chez-mimi"])


def test_outage_switch(registry: FakeRegistry, capsys: pytest.CaptureFixture[str]) -> None:
    registry.statuses["le-petit-zinc"] = "live"

    assert set_status.main(["le-petit-zinc", "down"]) == 0
    assert registry.statuses["le-petit-zinc"] == "down"
    assert set_status.main(["le-petit-zinc", "live"]) == 0
    assert registry.statuses["le-petit-zinc"] == "live"

    assert set_status.main(["chez-mimi", "down"]) == 1
    assert "not_found" in capsys.readouterr().err


def test_the_live_booking_key_is_stable_and_short() -> None:
    """`GuestText` caps the key at 200 characters, and a guest name may take all 200 (#79)."""
    starts_at = datetime(2026, 10, 31, 20, 0, tzinfo=ZURICH)
    longest = ["--merchant", "m" * 61, "--guest", "K" * 200]
    key = demo_booking.idempotency_key(demo_booking.parser().parse_args(longest), starts_at)

    assert key == demo_booking.idempotency_key(demo_booking.parser().parse_args(longest), starts_at)
    assert len(key) <= 200
    other = demo_booking.parser().parse_args([*longest, "--key", "2"])
    assert demo_booking.idempotency_key(other, starts_at) != key


def test_the_live_booking_needs_no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("GATEWAY_TOKEN", "s3cret")

    settings = demo_booking.GatewaySettings(_env_file=None)

    assert settings.gateway_token is not None
    assert settings.gateway_token.get_secret_value() == "s3cret"
