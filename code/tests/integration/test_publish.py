"""Publishing an agent a build left waiting (#136), through `POST /merchants/{slug}/publish`."""

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from core.config import Settings
from core.db import merchants_t
from core.models import MerchantOverview
from merchant_platform.accounts import Accounts, MemoryOwnerStore, Signer
from merchant_platform.api import create_app
from merchant_platform.overview import registry_records

pytestmark = pytest.mark.integration

MerchantFactory = Callable[..., Awaitable[str]]


def settings() -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://unused/db")  # type: ignore[call-arg]


async def status_of(engine: AsyncEngine, slug: str) -> Any:
    async with engine.connect() as conn:
        return await conn.scalar(select(merchants_t.c.status).where(merchants_t.c.slug == slug))


async def test_the_owner_publishes_a_waiting_agent(
    engine: AsyncEngine, make_merchant: MerchantFactory
) -> None:
    slug = await make_merchant(live=False)
    owners = Accounts(MemoryOwnerStore(), Signer(b"k" * 32))
    token = await owners.issue_link_token(slug)
    app = create_app(settings(), accounts=owners, engine=engine, registry=registry_records(engine))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        stranger = await http.post(f"/merchants/{slug}/publish")
        await http.get("/login", params={"token": token})
        published = await http.post(f"/merchants/{slug}/publish")

    assert stranger.status_code == 401
    assert await status_of(engine, slug) == "live"
    overview = MerchantOverview.model_validate(published.json())
    assert overview.status == "live"
    assert overview.mcp_url.endswith(f"/m/{slug}/mcp")
