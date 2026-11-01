"""Export the management mockup's demo data as the fixture the web mock and the seed read (#131).

Runs the generators of `documentation/design/poc-v10/sesame-management.html` in Chromium, as the
page does (fixed seeds, clock at Sat 31 Oct, 19:40), then writes
`tests/fixtures/demo/la-graine-verte-management.json` in the shape of `ManagementFixture`
(`web/src/types.ts`). What the mockup doesn't generate (merchant, hours, tables, rules,
settings) is kept from the current file. The assistant's opening conversation goes to
`la-graine-verte-assistant.json`, as the web chat's items (`web/src/chat/state.ts`, without
`id` and `turn`): `ManagementFixture` has no field for it. Run from `code/`:

    uv run python scripts/export_management_fixture.py [MOCKUP]

The 29 days before today only have totals in the mockup: `channels` counts the bookings that
`demo_management.history_bookings` makes from them, as the seed does.
"""

import argparse
import asyncio
import html
import json
import re
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import demo_management
from playwright.async_api import async_playwright

from agent.requests import describe

MOCKUP = (
    Path(__file__).resolve().parents[2]
    / "documentation"
    / "design"
    / "poc-v10"
    / "sesame-management.html"
)
# The page's last line starts the live booking 6 s after opening: instead, expose the state.
ANCHOR = "setTimeout(liveBooking, 6000);"
EXPOSE = "window.__mockup = { d, liveBooking };"
STATE = """() => {
  const d = window.__mockup.d;
  return { bookings: structuredClone(d.bookings), blocked: [...d.blocked].sort(),
           events: structuredClone(d.events), logs: structuredClone(d.logs),
           history: structuredClone(d.history) };
}"""
FEED = "() => [...document.querySelectorAll('#feed > div')].map(el => [el.className, el.innerHTML])"
OWNER = "Camille"  # "Good evening, Camille"
KINDS = {"New booking": "new_booking", "Blocked": "blocked", "Cancelled": "cancelled"}


async def run_mockup(
    page_html: str,
) -> tuple[dict[str, Any], dict[str, Any], list[tuple[str, str]]]:
    """The mockup's state when it opens, its live booking, and the assistant's messages."""
    if page_html.count(ANCHOR) != 1:
        raise SystemExit(f"The mockup changed: {ANCHOR!r} not found once")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            # Wide enough for the assistant to open with its first conversation, as in the demo.
            page = await browser.new_page(viewport={"width": 1440, "height": 900})
            await page.set_content(page_html.replace(ANCHOR, EXPOSE), wait_until="load")
            state: dict[str, Any] = await page.evaluate(STATE)
            feed: list[tuple[str, str]] = await page.evaluate(FEED)
            await page.evaluate("window.__mockup.liveBooking()")
            live: dict[str, Any] = (await page.evaluate(STATE))["bookings"][-1]
        finally:
            await browser.close()
    return state, live, feed


def frame(data: dict[str, Any]) -> str:
    """A BATP frame as the codec sends it (`batp.codec.encode`, without the newline)."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def markdown(message: str) -> str:
    """An assistant message of the mockup as the web chat's markdown: only bold is expected."""
    text = re.sub(r"<b>(.*?)</b>", r"**\1**", message)
    if "<" in text:
        raise SystemExit(f"The mockup's assistant now writes more than bold: {message!r}")
    return html.unescape(text)


def notification(booking: dict[str, Any]) -> dict[str, Any]:
    """The `LiveEvent` of a new booking from the fixture's `reservations`."""
    row = {k: booking[k] for k in ("id", "time", "guest_name", "party_size", "table_label")}
    return {
        "type": "notification",
        "kind": "new_booking",
        "via": booking["via"],
        "date": booking["date"],
        "reservation": {**row, "via": booking["via"], "status": booking["status"]},
    }


def conversation(feed: list[tuple[str, str]], data: dict[str, Any]) -> dict[str, Any]:
    """The mockup's first view of the assistant: the latest booking through the agent tonight,
    then two questions the page answered with its real handlers."""
    tonight = [
        b
        for b in data["reservations"]
        if b["date"] == data["now"][:10]
        and b["via"] == "claude"
        and b["status"] == "confirmed"
        and b["time"] >= data["hours"]["services"][-1]["start"]
    ]
    recent = max(tonight, key=lambda b: b["created_at"])
    items: list[dict[str, Any]] = []
    for kind, message in feed:
        if kind.startswith("ping") and recent["guest_name"] in message:
            items.append({"type": "notification", "event": notification(recent)})
        elif kind == "msg me":
            items.append({"type": "user", "text": html.unescape(message)})
        elif kind == "msg bot":
            items.append({"type": "assistant", "text": markdown(message)})
        else:
            raise SystemExit(f"The mockup's opening conversation changed: {kind!r} {message!r}")
    return {"merchant": data["merchant"]["slug"], "opening": items}


def at(day: str | date, hhmm: str, tz: ZoneInfo) -> str:
    """The merchant's local `day` at `hhmm`, as an ISO 8601 instant."""
    return datetime.combine(date.fromisoformat(str(day)), time.fromisoformat(hhmm), tz).isoformat()


def bookings(state: dict[str, Any], tz: ZoneInfo) -> list[dict[str, Any]]:
    """The mockup's bookings as the fixture's `reservations`, R-001 onwards in order."""
    reservations = [
        {
            "id": b["id"],
            "date": b["day"],
            "time": b["time"],
            "guest_name": b["guest"],
            "party_size": b["party"],
            "table_label": b["table"],
            "via": b["via"],
            "status": b["status"],
            "cancelled_by": b.get("by"),
            "created_at": at(b["created"]["d"], b["created"]["t"], tz),
        }
        for b in state["bookings"]
    ]
    numbers = [int(b["id"].removeprefix("R-")) for b in reservations]
    if numbers != list(range(1, len(numbers) + 1)):
        raise SystemExit("The mockup's bookings are no longer R-001, R-002, … in order")
    return reservations


def history(
    state: dict[str, Any], reservations: list[dict[str, Any]], today: date
) -> list[dict[str, Any]]:
    """The mockup's 29 days of totals, then today's, counted from the bookings."""
    days = [
        {
            "date": row["day"],
            "bookings": row["bookings"],
            "guests": row["total"],
            "agent_guests": row["agent"],
            "cancelled": row["cancelled"],
        }
        for row in state["history"]
    ]
    on_today = [b for b in reservations if b["date"] == today.isoformat()]
    live_today = [b for b in on_today if b["status"] == "confirmed"]
    days.append(
        {
            "date": today.isoformat(),
            "bookings": len(live_today),
            "guests": sum(b["party_size"] for b in live_today),
            "agent_guests": sum(b["party_size"] for b in live_today if b["via"] == "claude"),
            "cancelled": sum(1 for b in on_today if b["status"] == "cancelled"),
        }
    )
    return days


def channels(
    days: list[dict[str, Any]], reservations: list[dict[str, Any]], today: date
) -> list[dict[str, Any]]:
    """Live bookings per channel: today's as booked, the days before as the seed makes them."""
    counts = Counter(
        b["via"]
        for b in reservations
        if b["date"] == today.isoformat() and b["status"] == "confirmed"
    )
    for row in days[:-1]:
        for _, via in demo_management.split_day(
            row["bookings"], row["guests"], row["agent_guests"]
        ):
            counts[via] += 1
    return [{"via": via, "bookings": n} for via, n in sorted(counts.items())]


def activity(events: list[dict[str, Any]], tz: ZoneInfo) -> list[dict[str, Any]]:
    """Home's feed, newest first as the mockup keeps it."""
    return [
        {
            "id": len(events) - n,
            "at": at(event["day"], event["t"], tz),
            "kind": KINDS[event["kind"]],
            "text": event["text"],
            "source": event["src"],
            "reservation_id": event["text"].split(",")[0] if event["kind"] != "Blocked" else None,
        }
        for n, event in enumerate(events)
    ]


def agent_requests(
    logs: list[dict[str, Any]], kept: dict[str, Any], today: date, tz: ZoneInfo
) -> list[dict[str, Any]]:
    """The Agent page's requests, newest first, worded by the server."""
    fallback = kept["rules"]["fallback_url"]  # the mockup's own is an invented address
    found = []
    for log in logs:
        response = log["res"]
        if response.get("error", {}).get("fallback_url"):
            response = {**response, "error": {**response["error"], "fallback_url": fallback}}
        request, answer = frame(log["req"]), frame(response)
        # Worded by the server, not the mockup: the mock serves what the real Agent page says
        # (#146; the mockup's "offered her" guesses a guest's gender the node can't know).
        title, text = describe(
            "Claude",
            log["intent"],
            log["status"],
            request,
            answer,
            max_party_size=kept["rules"]["max_party_size"],
            tz=tz,
        )
        found.append(
            {
                "id": int(log["key"].removeprefix("r-")),
                "at": at(today, log["t"], tz),
                "agent_name": "Claude",
                "intent": log["intent"],
                "status": log["status"],
                "refused": log["status"] >= 400,
                "title": title,
                "text": text,
                "request": request,
                "response": answer,
            }
        )
    return found


def blocked(keys: list[str], tz: ZoneInfo) -> list[dict[str, Any]]:
    """The blocked starts (`"YYYY-MM-DD HH:MM"` in the mockup)."""
    found = []
    for key in keys:
        day, hhmm = key.split(" ")
        found.append({"starts_at": at(day, hhmm, tz), "date": day, "time": hhmm})
    return found


def live_booking(live: dict[str, Any]) -> dict[str, Any]:
    """The live event of the booking the mockup makes 6 s after opening."""
    return notification(
        {
            "id": live["id"],
            "date": live["day"],
            "time": live["time"],
            "guest_name": live["guest"],
            "party_size": live["party"],
            "table_label": live["table"],
            "via": live["via"],
            "status": live["status"],
        }
    )


def fixture(state: dict[str, Any], live: dict[str, Any], kept: dict[str, Any]) -> dict[str, Any]:
    """The `ManagementFixture`: the mockup's data, and what it doesn't generate from `kept`."""
    today = datetime.fromisoformat(kept["now"]).date()
    tz = ZoneInfo(kept["merchant"]["timezone"])
    reservations = bookings(state, tz)
    days = history(state, reservations, today)
    return {
        "now": kept["now"],
        "merchant": {**kept["merchant"], "owner_name": OWNER},
        "hours": kept["hours"],
        "tables": kept["tables"],
        "rules": kept["rules"],
        "settings": kept["settings"],
        "history": days,
        "channels": channels(days, reservations, today),
        "reservations": reservations,
        "blocked": blocked(state["blocked"], tz),
        "activity": activity(state["events"], tz),
        "agent_requests": agent_requests(state["logs"], kept, today, tz),
        "live_booking": live_booking(live),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mockup", nargs="?", type=Path, default=MOCKUP)
    args = parser.parse_args(argv)
    kept = json.loads(demo_management.FIXTURE.read_text())
    state, live, feed = asyncio.run(run_mockup(args.mockup.read_text()))
    data = fixture(state, live, kept)
    opening = conversation(feed, data)
    for path, content in ((demo_management.FIXTURE, data), (demo_management.CONVERSATION, opening)):
        path.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n")
    print(
        f"{demo_management.FIXTURE.name}: {len(data['reservations'])} bookings, "
        f"{len(data['history'])} days of history, {len(data['agent_requests'])} agent requests; "
        f"{demo_management.CONVERSATION.name}: {len(opening['opening'])} items"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
