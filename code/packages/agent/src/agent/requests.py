"""The Agent page's request log, worded for the merchant (#146).

The BATP node stores each answered request with both frames (`merchant_node.requests_log`);
this turns one row into the title and text the page shows. Pure, and never raises: the frames
come from our node, but a row it can't word still gets a plain title.
"""

import json
from datetime import date, datetime, tzinfo
from typing import Any

from agent.words import short_day


def describe(
    agent: str,
    intent: str,
    status: int,
    request: str,
    response: str,
    *,
    max_party_size: int,
    tz: tzinfo,
) -> tuple[str, str]:
    """`(title, text)` for one answered request. `max_party_size` is the merchant's limit now."""
    params = _object(_object(_load(request)).get("params"))
    answer = _object(_load(response))
    booking = _object(_object(answer.get("result")).get("reservation"))
    guest = _text(booking.get("guest_name")) or _text(params.get("guest_name")) or "A guest"
    party = _int(booking.get("party_size")) or _int(params.get("party_size"))
    starts = _instant(booking.get("starts_at"), tz) or _instant(params.get("starts_at"), tz)

    if 200 <= status < 300:
        match intent:
            case "AVAILABILITY":
                day = _day(params.get("date"))
                time = _text(params.get("time"))
                what = time or "free times"
                title = f"{agent} checked {what}" + (f" for {_guests(party)}" if party else "")
                text = f"{short_day(day)}. " if day else ""
                return title, text + "Your agent offered the closest free times."
            case "BOOK":
                return f"{guest} booked through {agent}", _booking(starts, party, booking)
            case "AMEND":
                return f"{guest} changed their booking through {agent}", _booking(
                    starts, party, booking
                )
            case "CANCEL":
                return f"{guest} cancelled through {agent}", _booking(starts, party, {})
    match status:
        case 413 if party:
            if party > max_party_size:
                limit = f"Your limit is {max_party_size}"
            else:
                limit = "No table fits them"
            return (
                f"A group of {party} was sent to your booking page",
                f"{limit}, so the guest got your link instead.",
            )
        case 409 if intent == "CANCEL" or (intent == "AMEND" and _started(answer)):
            verb = "cancel" if intent == "CANCEL" else "change"
            return (
                f"{guest} tried to {verb} a booking that had started",
                "Bookings can only be changed before they start.",
            )
        case 409:
            return (
                f"{guest} asked for a time that had just gone",
                f"{agent} offered them another one.",
            )
        case 404:
            reference = _text(params.get("reservation_id"))
            known = f"Reference {reference}." if reference else "Nothing was changed."
            return f"{agent} asked about a booking you don't have", known
        case 503:
            return (
                f"{agent} was sent to your booking page",
                "Your agent was paused or couldn't answer, so the guest got your link instead.",
            )
        case 400:
            return f"{agent} sent a request your agent couldn't read", "Nothing was changed."
    return f"{agent}: {intent.lower()}, answered {status}", ""


def _booking(starts: datetime | None, party: int | None, booking: dict[str, Any]) -> str:
    parts = []
    if starts:
        parts += [short_day(starts.date()), f"{starts:%H:%M}"]
    if party:
        parts.append(_guests(party))
    table = _text(booking.get("table_label"))
    if table:
        parts.append(f"table {table}")
    return ", ".join(parts) + "." if parts else ""


def _started(answer: dict[str, Any]) -> bool:
    """A 409 the node sent because the booking had already started (`_check_not_started`)."""
    message = _text(_object(answer.get("error")).get("message")) or ""
    return "already started" in message


def _guests(n: int) -> str:
    return "1 guest" if n == 1 else f"{n} guests"


def _load(frame: str) -> Any:
    try:
        return json.loads(frame)
    except ValueError:
        return None


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _instant(value: Any, tz: tzinfo) -> datetime | None:
    try:
        moment = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None
    if moment is None or moment.tzinfo is None:
        return None
    return moment.astimezone(tz)
