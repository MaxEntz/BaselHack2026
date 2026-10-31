"""The demo's live booking (#131): Kim Weber, 2 guests tonight at 20:00 at La Graine Verte.

It goes through the gateway's `create_reservation`, the tool Claude calls, so the merchant sees
the same "New booking via Claude" as for a guest in Claude. Run from `code/`, with the gateway up
(`make gateway`; on the team server, over SSH against its local port):

    uv run python scripts/demo_booking.py
    uv run python scripts/demo_booking.py --url http://127.0.0.1:8001/mcp --date 2026-11-01

The idempotency key is made of the arguments: running it twice books once. A booking cancelled
since then isn't booked again (the key replays it): `--key` with any word gives a new one.
"""

import argparse
import asyncio
import hashlib
import sys
from collections.abc import Sequence
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from core.config import Settings

GATEWAY_URL = "http://127.0.0.1:8001/mcp"
MERCHANT = "la-graine-verte"
GUEST = "Kim Weber"


class GatewaySettings(Settings):
    """`MERCHANT_TZ` and `GATEWAY_TOKEN` from the environment and `.env`; no database needed."""

    database_url: str = ""


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="The demo's live booking, through the gateway.")
    parser.add_argument("--url", default=GATEWAY_URL, help=f"gateway MCP URL ({GATEWAY_URL})")
    parser.add_argument("--merchant", default=MERCHANT, metavar="SLUG")
    parser.add_argument("--date", type=date.fromisoformat, help="YYYY-MM-DD (default today)")
    parser.add_argument("--time", type=time.fromisoformat, default=time(20, 0), metavar="HH:MM")
    parser.add_argument("--party-size", type=int, default=2)
    parser.add_argument("--guest", default=GUEST)
    parser.add_argument("--key", default="", help="any word, for a new booking with these values")
    return parser


def idempotency_key(args: argparse.Namespace, starts_at: datetime) -> str:
    """The same for the same arguments, and short whatever the guest's name (`GuestText`)."""
    values = (args.merchant, starts_at.isoformat(), str(args.party_size), args.guest, args.key)
    return f"demo-booking-{hashlib.sha256(repr(values).encode()).hexdigest()[:32]}"


async def book(url: str, arguments: dict[str, Any], token: str | None) -> dict[str, Any]:
    """`create_reservation`'s result: `{"reservation": …}`, or a §8.4 error."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with (
        httpx2.AsyncClient(headers=headers, timeout=10) as http,
        Client(streamable_http_client(url, http_client=http)) as client,
    ):
        result = await client.call_tool("create_reservation", arguments)
    if result.is_error or result.structured_content is None:
        raise RuntimeError(f"create_reservation failed: {result.content}")
    content: dict[str, Any] = result.structured_content["result"]
    return content


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    settings = GatewaySettings()
    tz = ZoneInfo(settings.merchant_tz)
    starts_at = datetime.combine(args.date or datetime.now(tz).date(), args.time, tz)
    arguments = {
        "merchant": args.merchant,
        "starts_at": starts_at.isoformat(),
        "party_size": args.party_size,
        "guest_name": args.guest,
        "idempotency_key": idempotency_key(args, starts_at),
    }
    token = settings.gateway_token.get_secret_value() if settings.gateway_token else None
    label = f"{args.merchant} {starts_at:%Y-%m-%d %H:%M}"
    try:
        result = asyncio.run(book(args.url, arguments, token))
    except ExceptionGroup as group:  # the MCP client's task group: nothing listening, a 401…
        http, other = group.split(httpx2.HTTPError)
        if http is None or other is not None:
            raise
        print(f"{label}: gateway at {args.url}: {http.exceptions[0]}", file=sys.stderr)
        print("Is it up (`make gateway`), and is GATEWAY_TOKEN right?", file=sys.stderr)
        return 1
    if "error" in result:
        print(f"{label}: {result['error']}: {result['message']}", file=sys.stderr)
        if result.get("fallback_url"):
            print(f"Fallback: {result['fallback_url']}", file=sys.stderr)
        return 1
    reservation = result["reservation"]
    summary = (
        f"{label}: {reservation['id']}, {reservation['party_size']} guests, "
        f"table {reservation['table_label']}, via {reservation['via']}, {reservation['status']}"
    )
    if reservation["status"] == "cancelled":  # a replay: the merchant gets no notification
        print(summary, file=sys.stderr)
        print(
            "Already booked with these values, then cancelled: `make demo-reset`, "
            'or BOOKING="--key 2" for a new booking.',
            file=sys.stderr,
        )
        return 1
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
