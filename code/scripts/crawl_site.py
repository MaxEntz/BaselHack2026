"""Crawl a website as the builder does (#21) and print what was read, without storing it.

Run from `code/`. `--allow-private` lets it read a local site, such as the demo one:

    uv run scripts/crawl_site.py https://www.example-restaurant.ch/
    uv run scripts/crawl_site.py --allow-private http://localhost:8080/
"""

import argparse
import asyncio
import sys

from builder.crawl import crawl
from core.errors import InvalidInput
from core.models import StepEvent


async def show(event: StepEvent) -> None:
    duration = f" ({event.duration_ms} ms)" if event.duration_ms is not None else ""
    print(f"[{event.status}] {event.detail}{duration}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url")
    parser.add_argument("--allow-private", action="store_true", help="read local addresses")
    parser.add_argument("--max-pages", type=int, default=5)
    args = parser.parse_args()
    try:
        result = await crawl(
            args.url, show, max_pages=args.max_pages, allow_private=args.allow_private
        )
    except InvalidInput:
        return 1  # the error event above says why
    for page in result.pages:
        types = ", ".join(str(block.get("@type")) for block in page.json_ld) or "none"
        print(f"\n{page.url}\n  title: {page.title}\n  description: {page.description}")
        print(f"  text: {len(page.text)} chars, JSON-LD: {types}")
        print(f"  starts with: {page.text[:120]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
