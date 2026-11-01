"""build_agent pipeline: crawl -> extract -> validate -> provision, as step events; `deploy`
waits for the owner, who publishes the agent once the profile is checked (#136)."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.provisioning import NotBuilding, deploy, deprovision, generated_tools, provision
from builder.cache import CACHE_ROOT, BuildCache, crawl_key, extraction_key
from builder.crawl import CrawlResult, StepEmitter, crawl, pages_read
from builder.extract import PROMPT_VERSION, ExtractionFailed, ExtractionResult, extract_profile
from builder.site import save_site, site_pages
from builder.slugs import numbered
from builder.urls import normalize_url
from core.db import merchants_t
from core.errors import AgentUnavailable, InvalidInput, NotFound
from core.llm import LLMClient, LLMUnavailable
from core.models import BuildAgentOutput, MerchantProfile, SitePageEvent, StepEvent

log = logging.getLogger("builder.build")

StepName = Literal["crawl", "extract", "validate", "provision", "deploy"]
# The steps, and the screenshots of the pages the crawl read (#151).
BuildEmitter = Callable[[StepEvent | SitePageEvent], Awaitable[None]]
Crawl = Callable[[str, StepEmitter], Awaitable[CrawlResult]]

# Generated profiles, kept as proof: copy the ones worth committing to tests/fixtures/.
# Under .cache (ignored by git): a build never writes into the source tree.
GENERATED_DIR = CACHE_ROOT / "generated"
LLM_DOWN = "The AI service is unavailable right now. Please try again in a moment."
WAITING = "Waiting for you."
# The `deploy` step's detail once the agent is live.
LIVE_DETAIL = "Your agent is live."
# A build nobody published blocks its website this long; then another account may replace it.
ABANDONED_AFTER = timedelta(hours=24)
STEP_FAILED = "Something went wrong while creating your agent."


class BeingBuilt(InvalidInput):
    """The website has an agent waiting to be published, less than `ABANDONED_AFTER` old."""

    def __init__(
        self, slug: str, website: str, free_at: datetime, tz: ZoneInfo | None = None
    ) -> None:
        # On the merchant's clock, as everything else they read (AGENTS.md §13, #184).
        local = free_at.astimezone(tz or UTC)
        when = f"{local:%Y-%m-%d %H:%M}" + ("" if tz else " UTC")
        super().__init__(
            f"{website} already has an agent waiting to be published ({slug}). Its owner can "
            f"finish it from their private link; anyone else can build it again after {when}."
        )
        self.slug = slug


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
    """Turns a website and the merchant's description into a provisioned agent (AGENTS.md
    §8.3): crawl, extract, validate and provision, streamed as step events. The agent stays
    `building`, hidden from guests, until its owner publishes it (`publish`)."""

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
        tz: ZoneInfo | None = None,
    ) -> None:
        self._llm = llm
        self._engine = engine
        self._model = model
        self._mcp_url = mcp_url
        self._cache = cache or BuildCache()
        self._crawl = crawl
        self._generated_dir = generated_dir
        self._tz = tz  # the merchants' zone, for the times they read; None: UTC

    async def build_agent(
        self, website: str, description: str, on_event: BuildEmitter
    ) -> BuildAgentOutput:
        """Build and provision the merchant's agent, streaming the five steps; `deploy` waits
        for the owner (`publish`), so guests and agents don't see it yet (#136).

        Raises `AlreadyProvisioned` before any step when the site already has a published
        agent, `BeingBuilt` when one waits to be published, and `InvalidInput` (crawl,
        extraction) or `AgentUnavailable` (LLM) after the failing step reported its error.
        """
        site = normalize_url(website)
        await self._claim_site(site)
        started = time.monotonic()

        crawled = await self._crawl_step(site, on_event)
        for page in site_pages(crawled.pages):
            await on_event(SitePageEvent(page=page))
        extracted = await self._extract_steps(site, crawled, description, on_event)
        profile = extracted.profile.model_copy(
            update={"slug": await self._unique_slug(extracted.profile.slug)}
        )

        # Set as soon as provision commits: only what this build created may be undone. A
        # failed provision (SlugTaken: a concurrent build got the slug) belongs to someone else.
        created = finished = False

        async def provisioned() -> str:
            nonlocal created
            await provision(profile, engine=self._engine)
            created = True
            await save_site(self._engine, profile.slug, crawled.pages)
            return (
                f"{len(profile.tables)} tables, {profile.seats} seats. "
                "Hidden from guests until you publish."
            )

        try:
            await self._step("provision", on_event, provisioned)
            await on_event(StepEvent(step="deploy", status="waiting", detail=WAITING))
            finished = True
        finally:
            if created and not finished:
                # Stopped inside the provision step (error, or the merchant left and the turn
                # was cancelled): the owner never saw it, so nothing waits for them.
                await self._undo(profile.slug)
        self._save(profile)
        log.info(
            "build_agent",
            extra={"merchant": profile.slug, "duration_ms": _ms_since(started)},
        )
        return BuildAgentOutput(
            profile=profile,
            status="building",
            assumptions=tuple(extracted.assumptions),
            facts=tuple(extracted.facts),
            flags=tuple(extracted.flags),
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
            return pages_read(cached.pages)

        await self._step("crawl", on_event, replay)
        return cached

    async def _extract_steps(
        self, site: str, crawled: CrawlResult, description: str, on_event: StepEmitter
    ) -> ExtractionResult:
        key = extraction_key(crawled, description, self._model, PROMPT_VERSION)
        cached = self._cache.get_extraction(key)
        log.info("builder_cache", extra={"stage": "extract", "hit": cached is not None})
        # The step running now, and since when: `extract`, then `validate`.
        step: tuple[StepName, float] = ("extract", time.monotonic())
        await on_event(StepEvent(step="extract", status="running"))

        async def validating() -> None:
            nonlocal step
            name, started = step
            await on_event(StepEvent(step=name, status="done", duration_ms=_ms_since(started)))
            step = ("validate", time.monotonic())
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
            await self._failed(on_event, step, str(error))
            raise
        except LLMUnavailable as error:
            await self._failed(on_event, step, LLM_DOWN)
            raise AgentUnavailable(LLM_DOWN) from error
        except Exception:
            await self._failed(on_event, step, STEP_FAILED)
            raise
        await on_event(
            StepEvent(
                step="validate",
                status="done",
                detail="Hours, tables and links are valid.",
                duration_ms=_ms_since(step[1]),
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

    async def _claim_site(self, site: str) -> None:
        """Make sure no agent holds `site`: a published one refuses the build, one waiting to be
        published does for `ABANDONED_AFTER`, then it is replaced (#136)."""
        for slug, status, created_at in await self._merchants_with_site(site):
            if status != "building":
                raise AlreadyProvisioned(slug, site)
            free_at = created_at + ABANDONED_AFTER
            if datetime.now(UTC) < free_at:
                raise BeingBuilt(slug, site, free_at, self._tz)
            log.info("build_replaced", extra={"merchant": slug})
            await deprovision(slug, engine=self._engine)

    async def _merchants_with_site(self, site: str) -> list[tuple[str, str, datetime]]:
        """The merchants whose website normalizes to `site`. Only the rows naming its host are
        read: the registry stores websites as `HttpUrl` writes them, with an ASCII host."""
        columns = merchants_t.c
        host = urlsplit(site).hostname or ""
        query = select(columns.slug, columns.website, columns.status, columns.created_at).where(
            func.lower(columns.website).contains(host, autoescape=True)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(query)).all()
        found = []
        for slug, website, status, created_at in rows:
            try:
                if normalize_url(website) == site:
                    found.append((str(slug), str(status), created_at))
            except InvalidInput:
                # A stored website the normalizer refuses names no site this build could claim.
                log.debug("registry website skipped", extra={"slug": slug})
                continue
        # A published agent wins over a leftover, whatever their order.
        return sorted(found, key=lambda row: row[1] == "building")

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
            slug = numbered(base, number)
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


PAUSED = "Your agent is paused: resume it rather than publish it again."
# `deploy` found the agent neither live nor paused: built again meanwhile (#220).
CHANGED = "Your agent changed while it was being published. Check it, then publish it again."


async def publish(slug: str, on_event: StepEmitter, *, engine: AsyncEngine) -> None:
    """The `deploy` step a build left waiting: the merchant becomes `live` (#136).

    A merchant already live stays so; one paused (`down`) is refused, since publishing must
    not undo its owner's pause, even one made since the status was read here: `deploy` only
    moves a `building` merchant (#220). `NotFound` for an unknown merchant.
    """
    async with engine.connect() as conn:
        status = await conn.scalar(select(merchants_t.c.status).where(merchants_t.c.slug == slug))
    if status is None:
        raise NotFound(f"No merchant {slug!r}.")
    if status == "down":
        raise InvalidInput(PAUSED)
    if status == "live":
        await on_event(StepEvent(step="deploy", status="done", detail=LIVE_DETAIL))
        return
    started = time.monotonic()
    await on_event(StepEvent(step="deploy", status="running"))
    try:
        await deploy(slug, engine=engine)
    except NotBuilding as moved:
        # Published (another tab), paused, or deleted and built again since the read above.
        if moved.status == "live":
            await on_event(_deployed("done", LIVE_DETAIL, started))
            return
        refusal = PAUSED if moved.status == "down" else CHANGED
        await on_event(_deployed("error", refusal, started))
        raise InvalidInput(refusal) from None
    except Exception:
        await on_event(_deployed("error", STEP_FAILED, started))
        raise
    log.info("publish_agent", extra={"merchant": slug})
    await on_event(_deployed("done", LIVE_DETAIL, started))


def _deployed(status: Literal["done", "error"], detail: str, started: float) -> StepEvent:
    """The end of the `deploy` step that `publish` started at `started`."""
    return StepEvent(step="deploy", status=status, detail=detail, duration_ms=_ms_since(started))
