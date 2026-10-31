"""build_agent pipeline: crawl -> extract -> validate -> provision -> deploy, as step events."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.provisioning import deploy, deprovision, generated_tools, provision
from builder.cache import BuildCache, crawl_key, extraction_key
from builder.crawl import CrawlResult, StepEmitter, crawl, normalize_url, page_name
from builder.extract import PROMPT_VERSION, ExtractionFailed, ExtractionResult, extract_profile
from builder.llm import LLMClient, LLMUnavailable
from core.db import merchants_t
from core.errors import AgentUnavailable, InvalidInput
from core.models import BuildAgentOutput, MerchantProfile, StepEvent

log = logging.getLogger("builder.build")

StepName = Literal["crawl", "extract", "validate", "provision", "deploy"]
Crawl = Callable[[str, StepEmitter], Awaitable[CrawlResult]]

# Generated profiles, kept as proof: copy the ones worth committing to tests/fixtures/.
# Under .cache (ignored by git): a build never writes into the source tree.
GENERATED_DIR = Path(__file__).parents[4] / ".cache" / "builder" / "generated"
LLM_DOWN = "The AI service is unavailable right now. Please try again in a moment."
STEP_FAILED = "Something went wrong while creating your agent."


class AlreadyProvisioned(InvalidInput):
    """This website already has an agent: update it rather than build a duplicate."""

    def __init__(self, slug: str, website: str) -> None:
        super().__init__(
            f"{website} already has an agent ({slug}). Offer to update its booking policy or "
            "floor plan instead of building a new one."
        )
        self.slug = slug


def gateway_mcp_url(public_url: str | None) -> str:
    """The protocol's MCP URL shown on the profile card: GATEWAY_PUBLIC_URL + /mcp."""
    if not public_url:
        return "http://localhost:8001/mcp"
    url = public_url if "://" in public_url else f"https://{public_url}"
    return f"{url.rstrip('/')}/mcp"


class Builder:
    def __init__(
        self,
        llm: LLMClient,
        engine: AsyncEngine,
        *,
        model: str,
        mcp_url: str,
        cache: BuildCache | None = None,
        crawl: Crawl = crawl,
        generated_dir: Path | None = GENERATED_DIR,
    ) -> None:
        self._llm = llm
        self._engine = engine
        self._model = model
        self._mcp_url = mcp_url
        self._cache = cache or BuildCache()
        self._crawl = crawl
        self._generated_dir = generated_dir

    async def build_agent(
        self, website: str, description: str, on_event: StepEmitter
    ) -> BuildAgentOutput:
        """Build, provision and publish the merchant's agent, streaming the five steps.

        Raises `AlreadyProvisioned` before any step when the site already has an agent, and
        `InvalidInput` (crawl, extraction) or `AgentUnavailable` (LLM) after the failing step
        reported its error.
        """
        site = normalize_url(website)
        existing = await self._merchant_with_site(site)
        if existing is not None:
            raise AlreadyProvisioned(existing, site)
        started = time.monotonic()

        crawled = await self._crawl_step(site, on_event)
        extracted = await self._extract_steps(site, crawled, description, on_event)
        profile = extracted.profile.model_copy(
            update={"slug": await self._unique_slug(extracted.profile.slug)}
        )

        async def provisioned() -> str:
            await provision(profile, engine=self._engine)
            return f"Created {len(profile.tables)} tables with {profile.seats} seats."

        async def deployed() -> str:
            await deploy(profile.slug, engine=self._engine)
            return "Your agent is live."

        live = False
        try:
            await self._step("provision", on_event, provisioned)
            await self._step("deploy", on_event, deployed)
            live = True
        finally:
            if not live:
                # Stopped between provision and deploy (error, or the merchant left and the
                # turn was cancelled): never leave a half-built merchant blocking its site.
                await self._undo(profile.slug)
        self._save(profile)
        log.info(
            "build_agent",
            extra={"merchant": profile.slug, "duration_ms": _ms_since(started)},
        )
        return BuildAgentOutput(
            profile=profile,
            status="live",
            assumptions=tuple(extracted.assumptions),
            tools=tuple(generated_tools(profile)),
            mcp_url=self._mcp_url,
        )

    async def _crawl_step(self, site: str, on_event: StepEmitter) -> CrawlResult:
        key = crawl_key(site)
        cached = self._cache.get_crawl(key)
        log.info("builder_cache", extra={"stage": "crawl", "hit": cached is not None})
        if cached is None:
            # The crawler reports its own `crawl` step, including errors.
            result = await self._crawl(site, on_event)
            self._cache.put_crawl(key, result)
            return result

        async def replay() -> str:
            names = ", ".join(["homepage", *(page_name(p.url) for p in cached.pages[1:])])
            count = len(cached.pages)
            return f"{count} page{'s' * (count > 1)} read: {names}"

        await self._step("crawl", on_event, replay)
        return cached

    async def _extract_steps(
        self, site: str, crawled: CrawlResult, description: str, on_event: StepEmitter
    ) -> ExtractionResult:
        key = extraction_key(site, description, self._model, PROMPT_VERSION)
        cached = self._cache.get_extraction(key)
        log.info("builder_cache", extra={"stage": "extract", "hit": cached is not None})
        current: list[tuple[StepName, float]] = [("extract", time.monotonic())]
        await on_event(StepEvent(step="extract", status="running"))

        async def validating() -> None:
            name, started = current[-1]
            await on_event(
                StepEvent(
                    step=name,
                    status="done",
                    detail="Combined your website with your description.",
                    duration_ms=_ms_since(started),
                )
            )
            current.append(("validate", time.monotonic()))
            await on_event(StepEvent(step="validate", status="running"))

        try:
            if cached is None:
                result = await extract_profile(
                    crawled, description, self._llm, on_validating=validating
                )
                self._cache.put_extraction(key, result)
            else:
                await validating()
                result = cached
        except ExtractionFailed as error:
            await self._failed(on_event, current[-1], str(error))
            raise
        except LLMUnavailable as error:
            await self._failed(on_event, current[-1], LLM_DOWN)
            raise AgentUnavailable(LLM_DOWN) from error
        except Exception:
            await self._failed(on_event, current[-1], STEP_FAILED)
            raise
        profile = result.profile
        await on_event(
            StepEvent(
                step="validate",
                status="done",
                detail=(
                    f"Profile complete: {len(profile.tables)} tables, {profile.seats} seats, "
                    f"{len(profile.services)} services."
                ),
                duration_ms=_ms_since(current[-1][1]),
            )
        )
        return result

    async def _step(
        self, name: StepName, on_event: StepEmitter, work: Callable[[], Awaitable[str]]
    ) -> None:
        started = time.monotonic()
        await on_event(StepEvent(step=name, status="running"))
        try:
            detail = await work()
        except Exception:
            await self._failed(on_event, (name, started), STEP_FAILED)
            raise
        await on_event(
            StepEvent(step=name, status="done", detail=detail, duration_ms=_ms_since(started))
        )

    async def _failed(
        self, on_event: StepEmitter, step: tuple[StepName, float], detail: str
    ) -> None:
        name, started = step
        await on_event(
            StepEvent(step=name, status="error", detail=detail, duration_ms=_ms_since(started))
        )

    async def _undo(self, slug: str) -> None:
        # Shielded: the cleanup must finish even while the turn is being cancelled.
        try:
            await asyncio.shield(deprovision(slug, engine=self._engine))
        except Exception:
            log.exception("build_undo_failed", extra={"merchant": slug})

    async def _merchant_with_site(self, site: str) -> str | None:
        """The live (or down) merchant with this website. A `building` leftover doesn't count:
        it was never published, and must not block the site."""
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(merchants_t.c.slug, merchants_t.c.website).where(
                        merchants_t.c.status != "building"
                    )
                )
            ).all()
        for slug, website in rows:
            try:
                if normalize_url(website) == site:
                    return str(slug)
            except InvalidInput:
                continue
        return None

    async def _unique_slug(self, base: str) -> str:
        """`base`, or `base-2`, `base-3`… when taken (slugs have no `%` or `_` for LIKE)."""
        async with self._engine.connect() as conn:
            taken = set(
                await conn.scalars(
                    select(merchants_t.c.slug).where(merchants_t.c.slug.like(f"{base}%"))
                )
            )
        slug, number = base, 1
        while slug in taken:
            number += 1
            suffix = f"-{number}"
            slug = base[: 61 - len(suffix)].rstrip("-") + suffix
        return slug

    def _save(self, profile: MerchantProfile) -> None:
        if self._generated_dir is None:
            return
        try:
            self._generated_dir.mkdir(parents=True, exist_ok=True)
            path = self._generated_dir / f"{profile.slug}.json"
            path.write_text(profile.model_dump_json(indent=2) + "\n", encoding="utf-8")
        except OSError:
            log.warning("generated_profile_not_saved", extra={"merchant": profile.slug})


def _ms_since(started: float) -> int:
    return round((time.monotonic() - started) * 1000)
