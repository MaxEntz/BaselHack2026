"""The demo's live booking (#131): Kim Weber, 2 guests tonight at 20:00 at La Graine Verte.

It books the way Claude does since #254: through the gateway's `talk_to_merchant`, so the
merchant's agent proposes the booking, the script checks the proposal and confirms it, and the
merchant sees the same "New booking via Claude" and the same requests on the Agent page as for
a guest in Claude. Run from `code/`, with the gateway and the node up (`make gateway`,
`make node`; on the team server, against the gateway's local port):

    uv run python scripts/demo_booking.py
    uv run python scripts/demo_booking.py --url http://127.0.0.1:8001/mcp --date 2026-11-01

It lists the guest's bookings first: a second run books nothing. A booking cancelled since is no
longer listed, so the next run books it again. Every check reads what code wrote (the
proposal's action and summary, the booking), never the model's words: when the agent proposes
nothing, or something else, the script prints its reply and exits 1 without confirming.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from uuid import uuid4
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


class Refused(Exception):
    """The gateway or the agent didn't book: the lines to print, on stderr."""


@dataclass(frozen=True)
class Booking:
    """What to book, and how to recognise it in what code wrote."""

    merchant: str
    starts_at: datetime
    party_size: int
    guest: str

    @property
    def label(self) -> str:
        return f"{self.merchant} {self.starts_at:%Y-%m-%d %H:%M}"

    def request(self) -> str:
        """The message to the merchant's agent, as a guest's agent would write it."""
        return (
            f"Please book a table for {self.party_size} on {self.starts_at:%A} "
            f"{self.starts_at.day} {self.starts_at:%B %Y} at {self.starts_at:%H:%M}, "
            f"under the name {self.guest}."
        )

    def proposed_in(self, pending: dict[str, Any] | None) -> bool:
        """Whether `pending` books this: its summary is written by code from the proposal's own
        arguments (`agent.guest_tools`)."""
        if not pending or pending.get("action") != "book":
            return False
        summary = str(pending.get("summary", ""))
        day = f"{self.starts_at.day} {self.starts_at:%B %Y} at {self.starts_at:%H:%M},"
        return (
            summary.startswith(f"A table for {self.party_size} at ")
            and day in summary
            and summary.endswith(f"under the name {self.guest}.")
        )

    def matches(self, reservation: dict[str, Any]) -> bool:
        """Whether `reservation` is this booking, confirmed."""
        return (
            reservation.get("merchant") == self.merchant
            and datetime.fromisoformat(reservation["starts_at"]) == self.starts_at
            and reservation.get("party_size") == self.party_size
            and reservation.get("guest_name") == self.guest
            and reservation.get("status") == "confirmed"
        )

    def line(self, reservation: dict[str, Any]) -> str:
        return (
            f"{self.label}: {reservation['id']}, {reservation['party_size']} guests, "
            f"table {reservation['table_label']}, via {reservation['via']}, "
            f"{reservation['status']}"
        )


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="The demo's live booking, through the gateway.")
    parser.add_argument("--url", default=GATEWAY_URL, help=f"gateway MCP URL ({GATEWAY_URL})")
    parser.add_argument("--merchant", default=MERCHANT, metavar="SLUG")
    parser.add_argument("--date", type=date.fromisoformat, help="YYYY-MM-DD (default today)")
    parser.add_argument("--time", type=time.fromisoformat, default=time(20, 0), metavar="HH:MM")
    parser.add_argument("--party-size", type=int, default=2)
    parser.add_argument("--guest", default=GUEST)
    return parser


async def call(client: Client, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """A tool's result; a §8.4 error raises `Refused` with its lines."""
    result = await client.call_tool(tool, arguments)
    if result.is_error or result.structured_content is None:
        raise RuntimeError(f"{tool} failed: {result.content}")
    content: dict[str, Any] = result.structured_content["result"]
    if "error" in content:
        raise Refused(f"{content['error']}: {content['message']}", *fallback(content))
    return content


def fallback(answer: dict[str, Any]) -> list[str]:
    return [f"Fallback: {answer['fallback_url']}"] if answer.get("fallback_url") else []


async def talk(client: Client, booking: Booking, message: str, **turn: str) -> dict[str, Any]:
    """One message to the merchant's agent, with a fresh key."""
    arguments = {"merchant": booking.merchant, "message": message, "idempotency_key": str(uuid4())}
    return await call(client, "talk_to_merchant", {**arguments, **turn})


async def book(client: Client, booking: Booking) -> tuple[dict[str, Any], bool]:
    """The booking, and whether it existed already. `Refused` when the agent proposes anything
    else than `booking`, or doesn't book it on the confirm."""
    mine = await call(client, "get_my_reservations", {"guest_name": booking.guest})
    for reservation in mine["reservations"]:
        if booking.matches(reservation):
            return reservation, True
    proposed = await talk(client, booking, booking.request())
    if not booking.proposed_in(proposed.get("pending")):
        raise Refused(
            "the agent proposed no such booking. It said:", proposed["reply"], *fallback(proposed)
        )
    done = await talk(
        client,
        booking,
        "Yes, please book it.",
        conversation=proposed["conversation"],
        confirm=proposed["pending"]["id"],
    )
    reservation = done.get("reservation")
    if not reservation or not booking.matches(reservation):
        raise Refused("the agent didn't book it. It said:", done["reply"], *fallback(done))
    return reservation, False


async def run(
    url: str, booking: Booking, token: str | None
) -> tuple[dict[str, Any], bool] | Refused:
    """`book` over one MCP session. A refusal is returned: raised inside the client's task group,
    it would come out of it as an `ExceptionGroup`."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with (
        httpx2.AsyncClient(headers=headers, timeout=60) as http,
        Client(streamable_http_client(url, http_client=http)) as client,
    ):
        try:
            return await book(client, booking)
        except Refused as refused:
            return refused


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    settings = GatewaySettings()
    tz = ZoneInfo(settings.merchant_tz)
    starts_at = datetime.combine(args.date or datetime.now(tz).date(), args.time, tz)
    booking = Booking(args.merchant, starts_at, args.party_size, args.guest)
    token = settings.gateway_token.get_secret_value() if settings.gateway_token else None
    try:
        outcome = asyncio.run(run(args.url, booking, token))
    except ExceptionGroup as group:  # the MCP client's task group: nothing listening, a 401…
        http, other = group.split(httpx2.HTTPError)
        if http is None or other is not None:
            raise
        print(f"{booking.label}: gateway at {args.url}: {http.exceptions[0]}", file=sys.stderr)
        print("Is it up (`make gateway`), and is GATEWAY_TOKEN right?", file=sys.stderr)
        return 1
    if isinstance(outcome, Refused):
        first, *rest = outcome.args
        print(f"{booking.label}: {first}", *rest, sep="\n", file=sys.stderr)
        return 1
    reservation, existed = outcome
    print(booking.line(reservation))
    if existed:
        print("Already booked with these values: nothing sent to the agent.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
