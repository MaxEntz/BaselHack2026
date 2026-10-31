"""Merchants for the integration tests (Postgres fixtures: `tests/conftest.py`)."""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.provisioning import deploy, deprovision, provision
from core.models import MerchantProfile

MerchantFactory = Callable[..., Awaitable[str]]


@pytest.fixture
async def make_merchant(
    engine: AsyncEngine, profile_data: dict[str, Any]
) -> AsyncIterator[MerchantFactory]:
    """Provision Le Petit Zinc under a unique slug, with profile overrides; dropped afterwards.

    `live=False` leaves it in `building`. The session database is shared between tests.
    """
    slugs: list[str] = []

    async def make(*, live: bool = True, **overrides: Any) -> str:
        slug = f"zinc-{uuid4().hex[:8]}"
        profile = MerchantProfile.model_validate({**profile_data, **overrides, "slug": slug})
        await provision(profile, engine=engine)
        slugs.append(slug)
        if live:
            await deploy(slug, engine=engine)
        return slug

    yield make
    for slug in slugs:
        await deprovision(slug, engine=engine)
