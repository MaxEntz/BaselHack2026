"""The builder's cache across a change of `Page` (#74 added `anchors`) or of a site (#148)."""

import json
from pathlib import Path

from builder.cache import BuildCache, crawl_key, extraction_key
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


def test_an_extraction_is_keyed_on_what_the_crawl_read(tmp_path: Path) -> None:
    def crawl(text: str) -> CrawlResult:
        return CrawlResult(website=SITE, pages=[Page(SITE, "La Graine Verte", text, [])])

    def key(crawled: CrawlResult) -> str:
        return extraction_key(crawled, "Cantine végétale", "gpt-6.1-terra", "v3.0")

    cache = BuildCache("read_write", tmp_path)
    cache.put_crawl(crawl_key(SITE), crawl("Bols de saison"))
    replayed = cache.get_crawl(crawl_key(SITE))

    assert replayed is not None
    assert key(replayed) == key(crawl("Bols de saison"))  # an offline replay still hits
    assert key(crawl("Cantine 100 % végétale")) != key(crawl("Bols de saison"))
