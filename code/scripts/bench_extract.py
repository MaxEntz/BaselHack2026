"""Bench the builder's extraction on the demo sites and real restaurant websites (#74).

Each site is crawled once (the real crawler), then extracted `--runs` times with the
builder's own prompt, schema, `finalize()` and retry, on the production model. Prints the
fields that went wrong on real sites, and exits 1 when a field differs from the expected
value in more than 1 run out of 5 (scaled to `--runs`).

Sites: the 3 demo sites and a fictional Basel brasserie with CHF prices (`bench_sites/`),
served by this script on free ports, and 2 real ones. `tests/integration/test_extraction_bench.py`
runs the local ones under the `llm` marker (#75). Run from `code/`, with OPENAI_API_KEY in
code/.env:

    uv run python scripts/bench_extract.py               # 5 runs per site
    uv run python scripts/bench_extract.py --runs 1 --site maison-albane
    uv run python scripts/bench_extract.py --local       # without network: the 4 local sites
"""

import argparse
import asyncio
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from openai import AsyncOpenAI

from builder.crawl import CrawlResult, crawl
from builder.extract import (
    PROMPT_VERSION,
    WEEKDAYS,
    ExtractionFailed,
    ExtractionResult,
    extract_profile,
)
from core.config import Settings
from core.llm import OpenAIClient
from core.models import MerchantProfile, StepEvent

DEMO_SITES = Path(__file__).resolve().parents[1] / "sites"
BENCH_SITES = Path(__file__).resolve().parent / "bench_sites"


ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
# The bench reads no database: DATABASE_URL may stay unset.
NO_DATABASE = "postgresql+psycopg://unused/bench"


def settings() -> Settings:
    """`code/.env`'s OPENAI_API_KEY, OPENAI_MODEL and BUILDER_REASONING_EFFORT, as the
    builder reads them."""
    return Settings(_env_file=ENV_FILE, database_url=NO_DATABASE)  # type: ignore[call-arg]


Check = Callable[[ExtractionResult], bool]


def _last_seating_by(limit: str) -> Check:
    # Last seating = service end - 60 min (AGENTS.md §6): an end at `limit` + 1 h at most.
    hours, minutes = map(int, limit.split(":"))
    latest = f"{hours + 1:02d}:{minutes:02d}"
    return lambda r: max(s.end for s in r.profile.services) <= latest


def _closed(*days: str) -> Check:
    """The weekly closing days named in the assumptions (#73): exactly `days`."""

    def check(result: ExtractionResult) -> bool:
        closing = " ".join(a for a in result.assumptions if a.startswith("Closed on"))
        return {day for day in WEEKDAYS if day in closing} == set(days)

    return check


def _group_size(expected: int) -> Check:
    """`expected`, or another value with an assumption saying where it came from."""

    def check(result: ExtractionResult) -> bool:
        if result.profile.max_party_size == expected:
            return True
        return any("group" in a.lower() or "party" in a.lower() for a in result.assumptions)

    return check


@dataclass(frozen=True)
class Site:
    name: str
    location: str | Path  # a real site's URL, or a local site's directory
    checks: dict[str, Check]

    @property
    def local(self) -> bool:
        return isinstance(self.location, Path)


@contextmanager
def served(site: Site) -> Iterator[str]:
    """The site's URL: a local one is served on a free port while the block runs."""
    if not isinstance(site.location, Path):
        yield site.location
        return
    handler = partial(SimpleHTTPRequestHandler, directory=str(site.location))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://localhost:{server.server_port}/"
    finally:
        server.shutdown()


SITES = (
    Site(
        "le-petit-zinc",
        DEMO_SITES / "le-petit-zinc",
        {
            "price €€": lambda r: r.profile.price_level == "€€",
            "services": lambda r: (
                [(s.start, s.end) for s in r.profile.services]
                == [("12:00", "14:00"), ("19:00", "22:00")]
            ),
            "group 6": _group_size(6),
            "fallback": lambda r: str(r.profile.fallback_url).endswith("/reservation.html"),
            "open daily": _closed(),
        },
    ),
    Site(
        "la-graine-verte",
        DEMO_SITES / "la-graine-verte",
        {
            "price €": lambda r: r.profile.price_level == "€",
            "services": lambda r: (
                [(s.start, s.end) for s in r.profile.services]
                == [("11:30", "14:30"), ("18:30", "21:30")]
            ),
            "fallback #": lambda r: str(r.profile.fallback_url).endswith("/#reserver"),
            "open daily": _closed(),
        },
    ),
    Site(
        "maison-albane",
        DEMO_SITES / "maison-albane",
        {
            "price €€€€": lambda r: r.profile.price_level == "€€€€",
            "services": lambda r: (
                [(s.start, s.end) for s in r.profile.services] == [("19:30", "22:30")]
            ),
            "fallback #": lambda r: str(r.profile.fallback_url).endswith("/#reserver"),
            "open daily": _closed(),
        },
    ),
    Site(
        "brasserie-chf",
        BENCH_SITES / "brasserie-chf",
        {
            "price €€€": lambda r: r.profile.price_level == "€€€",
            "kitchen hours": lambda r: (
                [(s.start, s.end) for s in r.profile.services]
                == [("11:30", "14:00"), ("18:00", "21:30")]
            ),
            "group 8": _group_size(8),
            "fallback #": lambda r: str(r.profile.fallback_url).endswith("/#reservation"),
            "closed Monday": _closed("Monday"),
        },
    ),
    Site(
        "kronenhalle",
        "https://www.kronenhalle.com/",
        {
            "last seating ≤ 21:30": _last_seating_by("21:30"),
            "fallback": lambda r: str(r.profile.fallback_url) == "https://www.kronenhalle.com/",
            "open daily": _closed(),
        },
    ),
    Site(
        "kunsthalle",
        "https://www.restaurant-kunsthalle.ch/",
        # Opening hours Monday to Saturday, no kitchen hours, no prices.
        {
            "closed Sunday": _closed("Sunday"),
            "opening hours": lambda r: (
                [(s.start, s.end) for s in r.profile.services] == [("11:30", "23:00")]
            ),
        },
    ),
)


async def _ignore(_: StepEvent) -> None:
    pass


async def _extract(crawled: CrawlResult, llm: OpenAIClient) -> ExtractionResult | str:
    try:
        return await extract_profile(crawled, "", llm)
    except ExtractionFailed as error:
        return str(error)


def _summary(profile: MerchantProfile) -> str:
    services = ",".join(f"{s.start}-{s.end}" for s in profile.services)
    return (
        f"{profile.price_level:4} {services:23} max={profile.max_party_size:<2} "
        f"{profile.fallback_url}"
    )


async def bench(site: Site, llm: OpenAIClient, runs: int) -> bool:
    """Print the site's runs; whether every check passed in all but 1 run out of 5."""
    with served(site) as url:
        crawled = await crawl(url, _ignore, allow_private=True)
    results = await asyncio.gather(*(_extract(crawled, llm) for _ in range(runs)))
    print(f"\n== {site.name} ({len(crawled.pages)} pages)")
    failures = dict.fromkeys(site.checks, 0)
    for result in results:
        if isinstance(result, str):
            print(f"  FAILED  {result}")
            failures = {name: count + 1 for name, count in failures.items()}
            continue
        wrong = [name for name, check in site.checks.items() if not check(result)]
        for name in wrong:
            failures[name] += 1
        print(f"  {_summary(result.profile)}  {'✗ ' + ', '.join(wrong) if wrong else '✓'}")
        for assumption in result.assumptions:
            print(f"      · {assumption}")
    allowed = runs // 5
    for name, count in failures.items():
        mark = "ok" if count <= allowed else "FAIL"
        print(f"  {mark:4} {name}: {runs - count}/{runs}")
    return all(count <= allowed for count in failures.values())


async def main() -> int:
    parser = argparse.ArgumentParser(description="Bench the builder's extraction (#74).")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--site", action="append", choices=[s.name for s in SITES])
    parser.add_argument("--local", action="store_true", help="only the sites served locally")
    args = parser.parse_args()
    env = settings()
    if env.openai_api_key is None:
        print("OPENAI_API_KEY is not set (code/.env)", file=sys.stderr)
        return 2
    client = AsyncOpenAI(api_key=env.openai_api_key.get_secret_value())
    llm = OpenAIClient(client, env.openai_model, reasoning_effort=env.builder_reasoning_effort)
    effort = env.builder_reasoning_effort
    print(f"model {env.openai_model}, effort {effort}, prompt {PROMPT_VERSION}")
    sites = [
        s for s in SITES if (not args.site or s.name in args.site) and (s.local or not args.local)
    ]
    passed = [await bench(site, llm, args.runs) for site in sites]
    return 0 if all(passed) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
