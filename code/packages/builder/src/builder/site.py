"""The merchant's public site as the crawl read it, for Studio's canvas (#151): each page's
screenshot, streamed during the build and stored with it. Never used to fill the profile."""

from collections.abc import Sequence

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from builder.crawl import Page
from builder.urls import page_path
from core.db import merchant_sites_t
from core.models import PublicSite, SitePage


def site_page(page: Page) -> SitePage | None:
    """The page as Studio shows it; None when its screenshot couldn't be taken."""
    if page.shot is None:
        return None
    return SitePage(
        path=page_path(page.url),
        width=page.shot_width,
        height=page.shot_height,
        image=f"data:image/jpeg;base64,{page.shot}",
    )


def site_pages(pages: Sequence[Page]) -> list[SitePage]:
    return [shown for page in pages if (shown := site_page(page)) is not None]


async def save_site(engine: AsyncEngine, slug: str, pages: Sequence[Page]) -> None:
    """In `public`, so existing merchants need no re-provision; deleted with the merchant."""
    stored = [page.model_dump(mode="json") for page in site_pages(pages)]
    async with engine.begin() as conn:
        await conn.execute(delete(merchant_sites_t).where(merchant_sites_t.c.merchant_slug == slug))
        await conn.execute(insert(merchant_sites_t).values(merchant_slug=slug, pages=stored))


async def load_site(engine: AsyncEngine, slug: str) -> PublicSite:
    """No pages for a merchant built before #151, or whose screenshots failed."""
    async with engine.connect() as conn:
        stored = await conn.scalar(
            select(merchant_sites_t.c.pages).where(merchant_sites_t.c.merchant_slug == slug)
        )
    return PublicSite(pages=tuple(SitePage.model_validate(page) for page in stored or ()))
