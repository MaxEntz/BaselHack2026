"""`GET /merchants/{slug}` (#129, #132): what the management site shows about a merchant."""

from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from builder.build import gateway_mcp_url
from core.config import Settings
from core.models import MerchantOverview
from merchant_platform.registry import MerchantRecord

# The fictional restaurants of `code/sites/`; a rebuild of one may take a `-2` suffix (#22).
DEMO_SLUGS = ("le-petit-zinc", "la-graine-verte", "maison-albane")


def is_demo(slug: str) -> bool:
    return any(slug == demo or slug.startswith(f"{demo}-") for demo in DEMO_SLUGS)


def merchant_overview(
    record: MerchantRecord, owner_name: str | None, settings: Settings, now: datetime
) -> MerchantOverview:
    mcp = gateway_mcp_url(settings.gateway_public_url)
    host = urlsplit(mcp).netloc
    web = settings.platform_web_url.rstrip("/")
    return MerchantOverview(
        slug=record.slug,
        name=record.name,
        city=record.city,
        price_level=record.price_level,
        tags=record.tags,
        website=record.website,
        status=record.status,
        timezone=settings.merchant_tz,
        now=now.astimezone(ZoneInfo(settings.merchant_tz)),
        # The node's own address (ADR-0011): loopback only for now, shown as the product's.
        agent_address=f"batp://{host}/{record.slug}",
        mcp_url=mcp.removesuffix("/mcp") + f"/m/{record.slug}/mcp",
        management_url=f"{web}/manage/{record.slug}",
        owner_name=owner_name,
        demo=is_demo(record.slug),
        look=record.look,
    )
