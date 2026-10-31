"""Real build on the demo site: Playwright crawl + OpenAI extraction + provisioning.

Run by hand after a prompt change: `uv run pytest -m llm tests/integration/test_builder_live.py`
(needs OPENAI_API_KEY, Chromium and Docker).
"""

import threading
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from openai import AsyncOpenAI
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.provisioning import deprovision
from builder.build import Builder
from builder.cache import BuildCache
from builder.crawl import crawl
from builder.llm import OpenAIClient
from core.models import StepEvent

pytestmark = [pytest.mark.integration, pytest.mark.browser, pytest.mark.llm]

DEMO_SITE = Path(__file__).parents[2] / "sites" / "le-petit-zinc"


class _LlmEnv(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-6.1-terra"
    builder_reasoning_effort: str | None = "none"


@pytest.fixture
def demo_site() -> Iterator[str]:
    handler = partial(SimpleHTTPRequestHandler, directory=str(DEMO_SITE))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://localhost:{server.server_port}/"
    server.shutdown()


async def test_builds_le_petit_zinc_from_its_website(
    engine: AsyncEngine, demo_site: str, tmp_path: Path
) -> None:
    env = _LlmEnv()
    assert env.openai_api_key is not None
    openai = AsyncOpenAI(api_key=env.openai_api_key.get_secret_value(), max_retries=0)
    builder = Builder(
        OpenAIClient(openai, env.openai_model, reasoning_effort=env.builder_reasoning_effort),
        engine,
        model=env.openai_model,
        mcp_url="http://localhost:8001/mcp",
        cache=BuildCache("off"),
        crawl=partial(crawl, allow_private=True),
        generated_dir=tmp_path,
    )
    steps: list[StepEvent] = []

    async def emit(step: StepEvent) -> None:
        steps.append(step)

    # No description: the tables come from defaults, as an assumption; the site states the
    # group size ("en ligne jusqu'à 6 personnes", although its form offers up to 12, #74).
    output = await builder.build_agent(demo_site, "", emit)
    try:
        profile = output.profile
        assert set(profile.tags) >= {"vegetarian", "gluten-free", "terrace", "kid-friendly"}
        assert profile.price_level == "€€"
        assert [(s.start, s.end) for s in profile.services] == [
            ("12:00", "14:00"),
            ("19:00", "22:00"),
        ]
        assumptions = " ".join(output.assumptions).lower()
        assert "table" in assumptions
        assert profile.max_party_size == 6
        assert "closed on" not in assumptions  # "tous les jours" (#73)
        assert str(profile.fallback_url).endswith("/reservation.html")
        # The build waits for its owner before deploy (#136); facts, not wording.
        assert [s.step for s in steps if s.status == "done"] == [
            "crawl",
            "extract",
            "validate",
            "provision",
        ]
        assert (steps[-1].step, steps[-1].status) == ("deploy", "waiting")
        assert output.status == "building"
        facts = {fact.key: fact for fact in output.facts}
        assert facts["max_party_size"].value.startswith("6")
        assert facts["booking_page"].value.startswith("/reservation.html")
        # A quote, when there is one, really is on the page it names (ADR-0003).
        assert all(f.source is None or f.quote is None or f.source != "you" for f in facts.values())
        assert any("floor plan" in flag.title for flag in output.flags)
    finally:
        await deprovision(output.profile.slug, engine=engine)
