"""On-disk cache of crawls and extractions, so that a demo onboarding survives a Wi-Fi or API drop.

`BUILDER_CACHE`: `read_write` (default), `read_only` (use what is there, store nothing) or
`off`. Entries are JSON files under `code/.cache/builder/<kind>/<sha256>.json` (ignored by
git). An unreadable entry counts as a miss.
"""

import hashlib
import json
import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from builder.crawl import CrawlResult, Page
from builder.extract import ExtractionResult
from core.models import BuildFact, BuildFlag, MerchantProfile

log = logging.getLogger("builder.cache")

CacheMode = Literal["read_write", "read_only", "off"]
DEFAULT_ROOT = Path(__file__).parents[4] / ".cache" / "builder"


# Part of the crawl cache key: bump it when `Page` changes or a demo site is rewritten, so no
# stale crawl is replayed (#136; `anchors` came with #74; La Graine Verte's new page, #148).
CRAWL_VERSION = "c4"  # c4: `Page.look` (#149)


def crawl_key(website: str) -> str:
    return _sha256(json.dumps([website, CRAWL_VERSION]))


def extraction_key(crawled: CrawlResult, description: str, model: str, prompt_version: str) -> str:
    """Keyed on what the crawl read: a new crawl of a rewritten site never meets the extraction
    of its old text (#148)."""
    return _sha256(json.dumps([asdict(crawled), description, model, prompt_version]))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class BuildCache:
    def __init__(self, mode: CacheMode = "read_write", root: Path = DEFAULT_ROOT) -> None:
        self.mode = mode
        self.root = root

    def get_crawl(self, key: str) -> CrawlResult | None:
        data = self._read("crawl", key)
        if data is None:
            return None
        try:
            return CrawlResult(
                website=data["website"], pages=[Page(**page) for page in data["pages"]]
            )
        except (KeyError, TypeError):
            return None

    def put_crawl(self, key: str, result: CrawlResult) -> None:
        self._write("crawl", key, asdict(result))

    def get_extraction(self, key: str) -> ExtractionResult | None:
        data = self._read("extraction", key)
        if data is None:
            return None
        try:
            profile = MerchantProfile.model_validate(data["profile"])
            return ExtractionResult(
                profile=profile,
                assumptions=list(data["assumptions"]),
                facts=[BuildFact.model_validate(fact) for fact in data["facts"]],
                flags=[BuildFlag.model_validate(flag) for flag in data["flags"]],
            )
        except (KeyError, TypeError, ValueError):
            return None

    def put_extraction(self, key: str, result: ExtractionResult) -> None:
        self._write(
            "extraction",
            key,
            {
                "profile": result.profile.model_dump(mode="json"),
                "assumptions": result.assumptions,
                "facts": [fact.model_dump(mode="json") for fact in result.facts],
                "flags": [flag.model_dump(mode="json") for flag in result.flags],
            },
        )

    def _path(self, kind: str, key: str) -> Path:
        return self.root / kind / f"{key}.json"

    def _read(self, kind: str, key: str) -> dict[str, Any] | None:
        if self.mode == "off":
            return None
        try:
            data = json.loads(self._path(kind, key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _write(self, kind: str, key: str, data: dict[str, Any]) -> None:
        if self.mode != "read_write":
            return
        path = self._path(kind, key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write then rename: a crash mid-write never leaves a truncated entry.
            temporary = path.with_suffix(f".{os.getpid()}.tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            log.warning("builder_cache_write_failed", extra={"kind": kind})
