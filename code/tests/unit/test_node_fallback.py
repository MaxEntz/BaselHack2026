"""The node's own 503 looks up the merchant's booking page; a lookup that fails says so (#220)."""

import logging
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.service import AgentService
from batp.messages import Request
from core.errors import NotFound
from merchant_node.agents import MerchantAgents

REQUEST = Request(id="r-1", intent="TALK", merchant="le-petit-zinc")


def agents_whose_registry(raises: Exception, monkeypatch: pytest.MonkeyPatch) -> MerchantAgents:
    agents = MerchantAgents(cast(AsyncEngine, None), cast(AgentService, None))

    async def registry(slug: str) -> Any:
        raise raises

    monkeypatch.setattr(agents, "_registry", registry)
    return agents


async def test_a_registry_that_fails_gives_no_fallback_and_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    agents = agents_whose_registry(OSError("connection refused"), monkeypatch)

    with caplog.at_level(logging.DEBUG, logger="merchant_node"):
        assert await agents.fallback_url(REQUEST) is None

    [line] = [r for r in caplog.records if r.getMessage() == "no fallback url"]
    assert (line.levelno, line.__dict__["merchant"]) == (logging.WARNING, "le-petit-zinc")
    assert line.exc_info is not None


async def test_an_unknown_merchant_gives_no_fallback_quietly(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Expected (a slug nobody published): a debug line, not a warning per request.
    agents = agents_whose_registry(NotFound("Unknown merchant"), monkeypatch)

    with caplog.at_level(logging.DEBUG, logger="merchant_node"):
        assert await agents.fallback_url(REQUEST) is None

    assert [r.levelno for r in caplog.records] == [logging.DEBUG]
