"""Seed, demo reset and outage switch (#10), with provisioning (#7) and the service (#8) faked."""

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import demo_reset
import pytest
import seed
import set_status

from agent import provisioning, service
from core.errors import InvalidInput, NotFound
from core.models import MerchantProfile, Reservation, Via

ZURICH = ZoneInfo("Europe/Zurich")


class FakeRegistry:
    """`agent.provisioning` as #7 specifies it."""

    def __init__(self) -> None:
        self.statuses: dict[str, str] = {}
        self.provisioned: list[str] = []

    async def provision(self, profile: MerchantProfile) -> None:
        if profile.slug in self.statuses:
            raise InvalidInput(f"{profile.slug} is already provisioned")
        self.provisioned.append(profile.slug)
        self.statuses[profile.slug] = "building"

    async def deploy(self, slug: str) -> None:
        self.statuses[slug] = "live"

    async def set_status(self, slug: str, status: str) -> None:
        if slug not in self.statuses:
            raise NotFound(f"Unknown merchant {slug}")
        self.statuses[slug] = status


class FakeAgent:
    """`create_reservation` of the AgentService protocol, idempotent like #8's."""

    def __init__(self) -> None:
        self.reservations: dict[str, Reservation] = {}
        self.missing = False

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
def registry(monkeypatch: pytest.MonkeyPatch) -> FakeRegistry:
    fake = FakeRegistry()
    for name in ("provision", "deploy", "set_status"):
        monkeypatch.setattr(provisioning, name, getattr(fake, name), raising=False)
    return fake


@pytest.fixture
def agent(monkeypatch: pytest.MonkeyPatch) -> FakeAgent:
    fake = FakeAgent()
    monkeypatch.setattr(service, "PgAgentService", lambda: fake, raising=False)
    return fake


def test_demo_mode_seeds_every_merchant_but_le_petit_zinc(registry: FakeRegistry) -> None:
    assert seed.main(["--except", "le-petit-zinc"]) == 0

    assert registry.provisioned == ["la-graine-verte", "maison-albane"]
    assert registry.statuses == {"la-graine-verte": "live", "maison-albane": "live"}


def test_seeding_again_keeps_existing_merchants_and_sets_them_live(
    registry: FakeRegistry,
) -> None:
    registry.statuses["le-petit-zinc"] = "down"

    assert seed.main(["--all"]) == 0

    assert registry.provisioned == ["la-graine-verte", "maison-albane"]
    assert set(registry.statuses.values()) == {"live"}


@pytest.mark.parametrize("argv", [[], ["--except", "chez-mimi"], ["--all", "--except", "x"]])
def test_seed_refuses_bad_options(registry: FakeRegistry, argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        seed.main(argv)

    assert exit_info.value.code == 2
    assert registry.provisioned == []


async def test_tonight_bookings_skip_past_times_and_never_book_twice(agent: FakeAgent) -> None:
    now = datetime(2026, 10, 31, 19, 15, tzinfo=ZURICH)

    assert await seed.seed_bookings(now) == 0
    assert await seed.seed_bookings(now) == 0

    booked = sorted(agent.reservations.values(), key=lambda r: r.starts_at)
    assert [(r.starts_at.isoformat(), r.party_size, r.via) for r in booked] == [
        ("2026-10-31T19:30:00+01:00", 4, "merchant"),
        ("2026-10-31T20:00:00+01:00", 6, "website"),
        ("2026-10-31T21:00:00+01:00", 3, "merchant"),
    ]
    assert {r.merchant for r in booked} == {"le-petit-zinc"}


async def test_bookings_without_le_petit_zinc_are_reported(agent: FakeAgent) -> None:
    agent.missing = True

    assert await seed.seed_bookings(datetime(2026, 10, 31, 12, 0, tzinfo=ZURICH)) == 4


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
