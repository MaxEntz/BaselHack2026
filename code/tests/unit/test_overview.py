"""`GET /merchants/{slug}` (#132): what the management site shows about its merchant."""

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from core.config import Settings
from core.models import MerchantOverview
from merchant_platform.accounts import OWNER_COOKIE, Accounts, MemoryOwnerStore, Signer
from merchant_platform.api import create_app
from merchant_platform.overview import MerchantRecord, is_demo, merchant_overview

SLUG = "la-graine-verte"
RECORD = MerchantRecord(
    slug=SLUG,
    name="La Graine Verte",
    city="Mulhouse",
    price_level="€",
    tags=("vegan", "vegetarian", "gluten-free"),
    website="http://localhost:8081/",
    status="live",
)


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": "postgresql+psycopg://u:p@localhost:5432/db",
        "gateway_public_url": "https://191-96-88-152.sslip.io",
        "platform_web_url": "https://sesame.example/",
    }
    return Settings(_env_file=None, **(values | overrides))  # type: ignore[call-arg]


async def registry(slug: str) -> MerchantRecord | None:
    return RECORD if slug == SLUG else None


def test_the_overview_gives_absolute_addresses_and_the_merchants_clock() -> None:
    at = datetime(2026, 10, 31, 18, 40, tzinfo=UTC)

    overview = merchant_overview(RECORD, "Léa", settings(), at)

    assert overview.agent_address == "batp://191-96-88-152.sslip.io/la-graine-verte"
    assert overview.mcp_url == "https://191-96-88-152.sslip.io/m/la-graine-verte/mcp"
    assert overview.management_url == "https://sesame.example/manage/la-graine-verte"
    assert overview.now.isoformat() == "2026-10-31T19:40:00+01:00"
    assert (overview.timezone, overview.owner_name, overview.demo) == ("Europe/Zurich", "Léa", True)


@pytest.mark.parametrize(
    ("slug", "demo"),
    [
        ("la-graine-verte", True),
        ("le-petit-zinc-2", True),
        ("maison-albane", True),
        ("chez-mimi", False),
        ("le-petit-zincs", False),
    ],
)
def test_demo_merchants_are_the_fictional_sites(slug: str, demo: bool) -> None:
    assert is_demo(slug) is demo


async def test_the_owner_reads_their_merchant_with_their_name() -> None:
    owners = Accounts(MemoryOwnerStore(), Signer(b"k" * 32))
    token = await owners.issue_link_token(SLUG, "Léa")
    owner = await owners.owner_for_token(token)
    assert owner is not None
    app = create_app(settings(), accounts=owners, registry=registry)

    transport = httpx.ASGITransport(app=app)
    cookies = {OWNER_COOKIE: owners.owner_cookie(owner)}
    async with httpx.AsyncClient(
        transport=transport, base_url="https://t", cookies=cookies
    ) as http:
        response = await http.get(f"/merchants/{SLUG}")

    assert response.status_code == 200
    overview = MerchantOverview.model_validate(response.json())
    assert (overview.name, overview.owner_name, overview.status) == (
        "La Graine Verte",
        "Léa",
        "live",
    )
    assert "profile" not in response.json()


@pytest.mark.parametrize(("registry_given", "status"), [(True, 404), (False, 503)])
async def test_a_merchant_missing_from_the_registry(registry_given: bool, status: int) -> None:
    app = create_app(
        settings(platform_merchant="chez-mimi", platform_web_url="http://localhost:5173"),
        registry=registry if registry_given else None,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://t") as http:
        response = await http.get("/merchants/chez-mimi")

    assert response.status_code == status
