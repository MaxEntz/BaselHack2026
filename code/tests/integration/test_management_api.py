"""The management site's routes (#129) on the real agent service."""

from collections.abc import Awaitable, Callable
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.service import PgAgentService
from core.config import Settings
from core.models import GuestsOutput
from merchant_platform.api import create_app

pytestmark = pytest.mark.integration

MerchantFactory = Callable[..., Awaitable[str]]  # the `make_merchant` fixture
TZ = ZoneInfo("Europe/Zurich")


@pytest.fixture
def service(engine: AsyncEngine) -> PgAgentService:
    return PgAgentService(engine, tz=TZ, clock=lambda: datetime(2026, 10, 31, 9, tzinfo=TZ))


def owner_client(service: PgAgentService, database_url: str, slug: str) -> httpx.AsyncClient:
    """A client signed in as `slug`'s owner: until #132, the development merchant's."""
    settings = Settings(_env_file=None, database_url=database_url, platform_merchant=slug)
    app = create_app(settings, agent=service)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_guests(
    service: PgAgentService, database_url: str, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant()
    for hour, guest in [(19, "Lena Becker"), (21, "Léna Becker"), (20, "Ada Lovelace")]:
        starts_at = datetime(2026, 11, 1, hour, tzinfo=TZ)
        await service.create_reservation(slug, starts_at, 2, guest, str(uuid4()), via="claude")

    async with owner_client(service, database_url, slug) as http:
        response = await http.get(f"/merchants/{slug}/guests", params={"q": "lena"})

    assert response.status_code == 200
    guests = GuestsOutput.model_validate(response.json()).guests
    assert [(g.guest_name, g.bookings, g.time) for g in guests] == [("Lena Becker", 2, "19:00")]


async def test_guests_of_an_unknown_merchant(service: PgAgentService, database_url: str) -> None:
    async with owner_client(service, database_url, "no-such-place") as http:
        response = await http.get("/merchants/no-such-place/guests")

    assert (response.status_code, response.json()["error"]) == (404, "not_found")
