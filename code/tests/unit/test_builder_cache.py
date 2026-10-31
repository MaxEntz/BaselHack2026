"""The builder's crawl cache across a change of `Page` (#74 added `anchors`)."""

import json
from pathlib import Path

from builder.cache import BuildCache, crawl_key
from builder.crawl import CrawlResult, Page

SITE = "http://localhost:8081/"


def test_a_crawl_keeps_its_anchors_through_the_cache(tmp_path: Path) -> None:
    cache = BuildCache("read_write", tmp_path)
    crawled = CrawlResult(
        website=SITE, pages=[Page(SITE, "La Graine Verte", "Bols", [], anchors=["reserver"])]
    )

    cache.put_crawl(crawl_key(SITE), crawled)

    assert cache.get_crawl(crawl_key(SITE)) == crawled


def test_a_crawl_cached_before_anchors_existed_still_loads(tmp_path: Path) -> None:
    entry = tmp_path / "crawl" / f"{crawl_key(SITE)}.json"
    entry.parent.mkdir()
    page = {"url": SITE, "title": "La Graine Verte", "text": "Bols", "json_ld": []}
    entry.write_text(json.dumps({"website": SITE, "pages": [page | {"description": None}]}))

    cached = BuildCache("read_write", tmp_path).get_crawl(crawl_key(SITE))

    assert cached is not None
    assert cached.pages[0].anchors == []
