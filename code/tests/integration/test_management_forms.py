"""The management site's forms (#133): hours, floor plan, rules, settings and blocked times,
through `PgAgentService` and the `/merchants/{slug}` routes, on a real Postgres 16."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.discovery import load_profile
from agent.provisioning import described_tools
from agent.service import PgAgentService
from core.config import Settings
from core.db import (
    blocked_slots_t,
    merchant_activity_t,
    merchant_conn,
    merchants_t,
    policy_t,
    tables_t,
)
from core.db import reservations_t as reservations
from core.errors import FormProblem, InvalidInput
from core.models import (
    MAX_KNOWLEDGE_ITEMS,
    BlockedTimes,
    FloorPlanOutput,
    Hours,
    KnowledgeNote,
    ManagementError,
    MerchantSettings,
    Rules,
    Service,
    TableSpec,
)
from merchant_platform.api import create_app

pytestmark = pytest.mark.integration

MerchantFactory = Callable[..., Awaitable[str]]  # the `make_merchant` fixture

TZ = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 10, 31, 9, tzinfo=TZ)
DAY = date(2026, 11, 2)
# Le Petit Zinc (tests/fixtures/le-petit-zinc.json): 12:00-14:00 and 19:00-22:00, groups of 6.
WEBSITE = "http://localhost:8080/"


def at(hhmm: str, day: date = DAY) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=TZ)


@pytest.fixture
def service(engine: AsyncEngine) -> PgAgentService:
    return PgAgentService(engine, tz=TZ, clock=lambda: NOW)


def owner_client(service: PgAgentService, database_url: str, slug: str) -> httpx.AsyncClient:
    settings = Settings(_env_file=None, database_url=database_url, platform_merchant=slug)
    app = create_app(settings, agent=service)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def registry(engine: AsyncEngine, slug: str) -> Any:
    async with engine.connect() as conn:
        return (await conn.execute(select(merchants_t).where(merchants_t.c.slug == slug))).one()


async def activity(engine: AsyncEngine, slug: str) -> list[tuple[str, dict[str, Any]]]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            select(merchant_activity_t.c.kind, merchant_activity_t.c.details)
            .where(merchant_activity_t.c.merchant_slug == slug)
            .order_by(merchant_activity_t.c.id)
        )
        return [(row.kind, row.details) for row in rows]


async def book(service: PgAgentService, slug: str, hhmm: str, party: int = 2) -> str:
    booked = await service.create_reservation(
        slug, at(hhmm), party, "Kim Weber", str(uuid4()), via="claude"
    )
    return booked.id


# Hours


async def test_hours_come_by_start_and_a_save_changes_the_starts(
    service: PgAgentService, database_url: str, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    async with owner_client(service, database_url, slug) as http:
        saved = await http.put(
            f"/merchants/{slug}/hours",
            json={"services": [{"start": "18:00", "end": "21:00"}, {"start": "11:30", "end": ""}]},
        )
        assert saved.status_code == 422  # the second service has no end
        saved = await http.put(
            f"/merchants/{slug}/hours",
            json={
                "services": [
                    {"start": "18:00", "end": "21:00"},
                    {"start": "11:30", "end": "14:30"},
                ]
            },
        )
        loaded = await http.get(f"/merchants/{slug}/hours")

    assert saved.status_code == 200
    expected = (Service(start="11:30", end="14:30"), Service(start="18:00", end="21:00"))
    assert Hours.model_validate(saved.json()).services == expected
    assert Hours.model_validate(loaded.json()).services == expected
    slots = await service.get_availability(slug, DAY, 2, "18:00")
    assert slots[0].starts_at == at("18:00")


async def test_the_same_hours_in_another_order_are_no_change(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    """Stored by start, as served: no activity line, and /m/<slug>/mcp words them alike (#201)."""
    slug = await make_merchant()

    await service.update_policy(
        slug, services=[Service(start="19:00", end="22:00"), Service(start="12:00", end="14:00")]
    )

    row = await registry(engine, slug)
    assert [s["start"] for s in row.profile["services"]] == ["12:00", "19:00"]
    assert await activity(engine, slug) == []


async def test_hours_stored_in_another_order_saved_by_start_are_no_change(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    """Stored before #201, out of order: the same hours saved by start write no line."""
    slug = await make_merchant()
    lunch, dinner = Service(start="12:00", end="14:00"), Service(start="19:00", end="22:00")
    async with merchant_conn(engine, slug) as conn:
        await conn.execute(
            update(policy_t).values(services=[dinner.model_dump(), lunch.model_dump()])
        )

    await service.update_policy(slug, services=[lunch, dinner])

    assert await activity(engine, slug) == []


# Floor plan


async def test_the_floor_plan_comes_back_in_the_order_it_was_saved(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(
        tables=[{"label": "T1", "seats": 2}, {"label": "T2", "seats": 4}], seats=6
    )
    plan = [
        TableSpec(label="T1", seats=2),
        TableSpec(label="Bar", seats=2),
        TableSpec(label="T2", seats=4),
    ]

    await service.update_tables(slug, plan)

    assert await service.get_tables(slug) == plan


@pytest.mark.parametrize(
    ("plan", "problem"),
    [
        ([("T1", 2)], {"code": "table_in_use", "table": "T2", "guest": "Kim Weber"}),
        (
            [("T1", 2), ("T2", 2)],
            {"code": "table_too_small", "table": "T2", "guest": "Kim Weber", "seats": 4},
        ),
    ],
    ids=["removed", "shrunk"],
)
async def test_a_floor_plan_that_strands_a_booking_names_the_table_and_the_guest(
    service: PgAgentService,
    database_url: str,
    make_merchant: MerchantFactory,
    plan: list[tuple[str, int]],
    problem: dict[str, Any],
) -> None:
    slug = await make_merchant(
        tables=[{"label": "T1", "seats": 2}, {"label": "T2", "seats": 4}], seats=6
    )
    await book(service, slug, "19:00", party=4)
    body = {"tables": [{"label": label, "seats": seats} for label, seats in plan]}

    async with owner_client(service, database_url, slug) as http:
        response = await http.put(f"/merchants/{slug}/tables", json=body)
        loaded = await http.get(f"/merchants/{slug}/tables")

    assert response.status_code == 422
    error = ManagementError.model_validate(response.json())
    assert error.problem is not None
    assert error.problem.model_dump(mode="json", exclude_defaults=True) == problem
    assert FloorPlanOutput.model_validate(loaded.json()).tables == (
        TableSpec(label="T1", seats=2),
        TableSpec(label="T2", seats=4),
    )


async def test_a_table_past_bookings_refer_to_names_their_guest(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(
        tables=[{"label": "T1", "seats": 2}, {"label": "T2", "seats": 4}], seats=6
    )
    start = NOW - timedelta(days=3)
    async with merchant_conn(engine, slug) as conn:
        table_id = await conn.scalar(select(tables_t.c.id).where(tables_t.c.label == "T1"))
        await conn.execute(
            insert(reservations).values(
                id="R-800",
                table_id=table_id,
                guest_name="Sara Muller",
                party_size=2,
                during=Range(start, start + timedelta(hours=2)),
                via="website",
            )
        )

    with pytest.raises(FormProblem) as refused:
        await service.update_tables(slug, [TableSpec(label="T2", seats=4)])

    problem = refused.value.problem
    assert (problem.code, problem.table, problem.guest) == ("table_in_use", "T1", "Sara Muller")


# Rules


async def activity_lines(engine: AsyncEngine, slug: str) -> list[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            select(merchant_activity_t.c.details).where(merchant_activity_t.c.merchant_slug == slug)
        )
    return [str(row.details.get("what")) for row in rows]


async def test_a_correction_changes_what_the_agents_next_prompt_reads(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(knowledge=None)
    tartare = KnowledgeNote(topic="menu", text="Le Tartare de bœuf, 28 CHF")
    dogs = KnowledgeNote(topic="practical", text="Les chiens sont les bienvenus.")
    await service.update_knowledge(slug, (), (tartare, dogs))

    change = await service.update_knowledge(
        slug, ("TARTARE", "parking"), (KnowledgeNote(topic="menu", text="Pas de tartare."),)
    )

    assert change.removed == ("Le Tartare de bœuf, 28 CHF",)
    assert change.added == ("Pas de tartare.",)
    profile = await load_profile(slug, engine=engine)  # as a new prompt would read it
    assert profile is not None
    assert profile.knowledge is not None
    assert [item.text for item in profile.knowledge.items] == [
        "Pas de tartare.",
        "Les chiens sont les bienvenus.",
    ]
    assert (profile.knowledge.items[0].source_url, profile.knowledge.items[0].quote) == (None, None)
    assert await activity_lines(engine, slug) == ["What your agent knows"] * 2


async def test_a_correction_that_changes_nothing_writes_nothing(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(knowledge=None)
    dogs = KnowledgeNote(topic="practical", text="Les chiens sont les bienvenus.")
    await service.update_knowledge(slug, (), (dogs,))

    change = await service.update_knowledge(slug, ("parking",), (dogs,))

    assert (change.removed, change.added) == ((), ())
    assert await activity_lines(engine, slug) == ["What your agent knows"]


async def test_removing_the_last_item_leaves_no_knowledge(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(knowledge=None)
    dogs = KnowledgeNote(topic="practical", text="Les chiens sont les bienvenus.")
    await service.update_knowledge(slug, (), (dogs,))

    await service.update_knowledge(slug, ("chiens",), ())

    assert (await registry(engine, slug)).profile["knowledge"] is None


async def test_a_correction_past_the_bounds_is_refused_and_changes_nothing(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(knowledge=None)
    notes = [KnowledgeNote(topic="menu", text=f"Plat {n}") for n in range(MAX_KNOWLEDGE_ITEMS + 1)]

    with pytest.raises(InvalidInput, match="remove something first"):
        await service.update_knowledge(slug, (), notes)

    assert (await registry(engine, slug)).profile.get("knowledge") is None
    assert await activity_lines(engine, slug) == []


async def test_saving_the_rules_updates_the_policy_the_registry_and_the_descriptions(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()

    rules = await service.update_rules(
        slug, 4, "http://localhost:8080/contact.html", "Free cancellation until noon."
    )

    assert (rules.max_party_size, rules.slot_length_min) == (4, 120)
    assert str(rules.fallback_url) == "http://localhost:8080/contact.html"
    assert rules.cancellation_policy == "Free cancellation until noon."
    row = await registry(engine, slug)
    assert row.fallback_url == "http://localhost:8080/contact.html"
    assert row.profile["max_party_size"] == 4
    assert row.profile["fallback_url"] == "http://localhost:8080/contact.html"
    assert row.profile["cancellation_policy"] == "Free cancellation until noon."
    # What an AI agent reads on /m/<slug>/mcp, with no restart.
    profile = await load_profile(slug, engine=engine)
    assert profile is not None
    assert rules.tools == tuple(described_tools(profile))
    assert "Groups up to 4" in rules.tools[0].description
    assert "Free cancellation" not in " ".join(t.description for t in rules.tools)
    assert await service.get_rules(slug) == rules
    with pytest.raises(Exception, match="up to 4 guests"):
        await service.get_availability(slug, DAY, 5)


async def test_an_identical_save_writes_no_activity_line(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()

    for _ in range(2):
        await service.update_rules(slug, 4, "http://localhost:8080/reservation.html", None)
    for _ in range(2):
        await service.update_settings(slug, "Le Petit Zinc", "Basel", "€€", ["terrace"])

    assert await activity(engine, slug) == [
        ("saved", {"what": "Booking rules"}),
        ("saved", {"what": "Settings"}),
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://booking.example/le-petit-zinc",
        "http://localhost:8081/#reserver",  # another demo site: same host, other port
    ],
)
async def test_a_booking_page_off_the_merchants_site_is_refused(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory, url: str
) -> None:
    slug = await make_merchant()

    with pytest.raises(FormProblem) as refused:
        await service.update_rules(slug, 4, url, None)

    assert refused.value.problem.code == "booking_page_off_site"
    row = await registry(engine, slug)
    assert row.fallback_url == "http://localhost:8080/reservation.html"
    assert row.profile["max_party_size"] == 6
    assert await activity(engine, slug) == []


async def test_a_booking_page_on_the_www_host_stays_on_the_site(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(
        website="https://zinc.example/", fallback_url="https://zinc.example/"
    )

    rules = await service.update_rules(slug, 6, "https://www.zinc.example/reserver", None)

    assert str(rules.fallback_url) == "https://www.zinc.example/reserver"


async def test_a_long_stored_policy_reads_as_one_paragraph_the_form_can_save_back(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(cancellation_policy="Call us.\n\n" + "Really. " * 100)

    rules = await service.get_rules(slug)

    assert rules.cancellation_policy is not None
    assert "\n" not in rules.cancellation_policy
    assert len(rules.cancellation_policy) <= 500
    assert rules.cancellation_policy.startswith("Call us. Really.")


async def test_an_unchanged_save_of_a_long_policy_keeps_its_text_and_writes_no_line(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    """The form shows the stored policy cut to one paragraph; sent back as it was shown, the
    full text stays (#201). Another text still replaces it."""
    long = "Call us.\n\n" + "Really. " * 100
    slug = await make_merchant(cancellation_policy=long)
    shown = await service.get_rules(slug)
    page = str(shown.fallback_url)

    saved = await service.update_rules(slug, 6, page, shown.cancellation_policy)

    assert saved == shown
    assert (await registry(engine, slug)).profile["cancellation_policy"] == long
    assert await activity(engine, slug) == []

    await service.update_rules(slug, 6, page, "Free cancellation until noon.")

    row = await registry(engine, slug)
    assert row.profile["cancellation_policy"] == "Free cancellation until noon."
    assert await activity(engine, slug) == [("saved", {"what": "Booking rules"})]


async def test_an_unchanged_save_of_a_blank_policy_writes_no_line(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(cancellation_policy="   ")
    shown = await service.get_rules(slug)

    await service.update_rules(slug, 6, str(shown.fallback_url), shown.cancellation_policy)

    assert shown.cancellation_policy is None
    assert await activity(engine, slug) == []


async def test_rules_over_http(
    service: PgAgentService, database_url: str, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    body = {
        "max_party_size": 4,
        "fallback_url": "http://localhost:8080/reservation.html",
        "cancellation_policy": "  ",
    }

    async with owner_client(service, database_url, slug) as http:
        saved = await http.put(f"/merchants/{slug}/rules", json=body)
        off_site = await http.put(
            f"/merchants/{slug}/rules", json=body | {"fallback_url": "https://elsewhere.example/"}
        )
        loaded = await http.get(f"/merchants/{slug}/rules")

    assert saved.status_code == 200
    assert Rules.model_validate(saved.json()).cancellation_policy is None
    assert off_site.status_code == 422
    assert off_site.json()["problem"] == {"code": "booking_page_off_site", "services": []}
    assert loaded.json() == saved.json()


# Settings


async def test_saving_the_settings_updates_the_registry_and_the_descriptions(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(location={"city": "Mulhouse", "lat": 47.75, "lng": 7.34})

    settings = await service.update_settings(
        slug, "Zinc & Co", "Basel", "€€€", ["vegan", "terrace", "vegan"]
    )

    assert settings == MerchantSettings(
        name="Zinc & Co",
        city="Basel",
        website=WEBSITE,  # type: ignore[arg-type]
        price_level="€€€",
        tags=("vegan", "terrace"),
    )
    row = await registry(engine, slug)
    assert (row.name, row.city, row.price_level, row.tags) == (
        "Zinc & Co",
        "Basel",
        "€€€",
        ["vegan", "terrace"],
    )
    assert row.profile["location"] == {"city": "Basel", "lat": 47.75, "lng": 7.34}
    assert row.profile["tags"] == ["vegan", "terrace"]
    rules = await service.get_rules(slug)
    assert rules.tools[0].description.startswith("Talk to the agent of Zinc & Co (Basel)")
    assert await service.get_settings(slug) == settings


async def test_a_blank_city_is_none(
    service: PgAgentService, database_url: str, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    body = {"name": "Le Petit Zinc", "city": " ", "price_level": "€", "tags": []}

    async with owner_client(service, database_url, slug) as http:
        saved = await http.put(f"/merchants/{slug}/settings", json=body)
        loaded = await http.get(f"/merchants/{slug}/settings")

    assert saved.status_code == 200
    assert MerchantSettings.model_validate(loaded.json()).city is None
    assert (
        (await service.get_rules(slug))
        .tools[0]
        .description.startswith("Talk to the agent of Le Petit Zinc (unknown city)")
    )


# Blocked times


async def test_a_day_s_blocked_times_are_set_in_one_save(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    booked = await book(service, slug, "20:00")

    first = await service.set_blocked(slug, DAY, ["20:00", "20:30", "20:00"])
    second = await service.set_blocked(slug, DAY, ["20:30", "21:00"])

    assert [(b.date, b.time) for b in first] == [(DAY, "20:00"), (DAY, "20:30")]
    assert [b.time for b in second] == ["20:30", "21:00"]
    slots = await service.get_availability(slug, DAY, 2, "20:30")
    assert {s.starts_at for s in slots}.isdisjoint({at("20:30"), at("21:00")})
    assert [r.id for r in await service.list_reservations(slug, DAY)] == [booked]
    assert [kind for kind, _ in await activity(engine, slug)] == [
        "new_booking",
        "blocked",
        "blocked",
    ]

    await service.set_blocked(slug, DAY, [])

    assert await service.list_blocked(slug) == []
    kind, details = (await activity(engine, slug))[-1]
    assert kind == "reopened"
    reopened = [datetime.fromisoformat(moment) for moment in details["times"]]
    assert reopened == [at("20:30"), at("21:00")]


async def test_blocking_a_time_that_is_not_a_start_changes_nothing(
    service: PgAgentService, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    await service.set_blocked(slug, DAY, ["19:00"])

    with pytest.raises(InvalidInput, match="21:30 is not a start time"):
        await service.set_blocked(slug, DAY, ["20:00", "21:30"])

    assert [b.time for b in await service.list_blocked(slug)] == ["19:00"]


async def test_only_blocked_times_to_come_are_listed(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    await service.set_blocked(slug, DAY, ["13:00"])
    later = PgAgentService(engine, tz=TZ, clock=lambda: at("13:00"))

    assert [b.time for b in await later.list_blocked(slug)] == []
    assert [b.time for b in await service.list_blocked(slug)] == ["13:00"]


async def test_a_save_keeps_the_day_s_past_blocked_times(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    """At 20:10 the form only knows 21:00: reopening it leaves 19:00 blocked and unnamed."""
    slug = await make_merchant()
    morning = PgAgentService(engine, tz=TZ, clock=lambda: NOW)
    await morning.set_blocked(slug, DAY, ["19:00", "21:00"])
    evening = PgAgentService(engine, tz=TZ, clock=lambda: at("20:10"))

    # 19:30 has passed: ignored, and the save changes nothing.
    assert [b.time for b in await evening.set_blocked(slug, DAY, ["19:30", "21:00"])] == ["21:00"]
    assert [b.time for b in await evening.set_blocked(slug, DAY, [])] == []

    async with merchant_conn(engine, slug) as conn:
        kept = list(await conn.scalars(select(blocked_slots_t.c.starts_at)))
    assert kept == [at("19:00")]
    kind, details = (await activity(engine, slug))[-1]
    assert kind == "reopened"
    assert [datetime.fromisoformat(t) for t in details["times"]] == [at("21:00")]


async def test_blocked_times_over_http(
    service: PgAgentService, database_url: str, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()

    async with owner_client(service, database_url, slug) as http:
        saved = await http.put(
            f"/merchants/{slug}/blocked", params={"date": "2026-11-02"}, json={"times": ["12:30"]}
        )
        loaded = await http.get(f"/merchants/{slug}/blocked")
        refused = await http.put(
            f"/merchants/{slug}/blocked", params={"date": "2026-11-02"}, json={"times": ["16:00"]}
        )

    assert saved.status_code == 200
    blocked = BlockedTimes.model_validate(loaded.json()).blocked
    assert [(b.starts_at, b.time) for b in blocked] == [(at("12:30"), "12:30")]
    assert loaded.json() == saved.json()
    assert (refused.status_code, refused.json()["error"]) == (422, "invalid_input")


# Every form


@pytest.mark.parametrize("path", ["/hours", "/tables", "/rules", "/settings", "/blocked"])
async def test_a_form_of_an_unknown_merchant_is_not_found(
    service: PgAgentService, database_url: str, path: str
) -> None:
    async with owner_client(service, database_url, "no-such-place") as http:
        response = await http.get(f"/merchants/no-such-place{path}")

    assert (response.status_code, response.json()["error"]) == (404, "not_found")


async def test_a_merchant_still_building_can_fill_its_forms(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(live=False)

    settings = await service.update_settings(slug, "Zinc", None, "€", [])

    assert settings.name == "Zinc"
    async with engine.connect() as conn:
        status = await conn.scalar(select(merchants_t.c.status).where(merchants_t.c.slug == slug))
    assert status == "building"


async def test_a_concurrent_form_save_keeps_the_registry_whole(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    """Rules and settings both rewrite the registry's profile: neither loses the other's."""
    slug = await make_merchant()

    await asyncio.gather(
        service.update_rules(slug, 3, "http://localhost:8080/reservation.html", None),
        service.update_settings(slug, "Zinc", "Basel", "€", []),
    )

    profile = (await registry(engine, slug)).profile
    assert (profile["max_party_size"], profile["name"]) == (3, "Zinc")


async def test_a_registry_profile_missing_a_location_still_takes_a_city(
    service: PgAgentService, engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(location=None)
    async with engine.begin() as conn:
        await conn.execute(update(merchants_t).where(merchants_t.c.slug == slug).values(city=None))

    await service.update_settings(slug, "Le Petit Zinc", "Basel", "€€", [])

    assert (await registry(engine, slug)).profile["location"] == {"city": "Basel"}
