"""The Agent page's request log, worded for the merchant (#146).

The BATP node stores each answered request with both frames (`merchant_node.requests_log`);
this turns one row into the title and text the page shows. Pure, and never raises: the frames
come from our node, but a row it can't word still gets a plain title.
"""

import json
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from typing import Any

from agent.times import hhmm
from agent.words import short_day
from core.errors import STARTED


@dataclass(frozen=True)
class _Request:
    """What one request's frames say, read leniently: what they don't say is None or empty."""

    agent: str
    params: dict[str, Any]
    answer: dict[str, Any]
    booking: dict[str, Any]
    guest: str
    party: int | None
    starts: datetime | None


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
    read = _read(agent, request, response, tz)
    worded = (
        _answered(read, intent)
        if 200 <= status < 300
        else _refused(read, intent, status, max_party_size)
    )
    return worded or (f"{agent}: {intent.lower()}, answered {status}", "")


def _read(agent: str, request: str, response: str, tz: tzinfo) -> _Request:
    params = _object(_object(_load(request)).get("params"))
    answer = _object(_load(response))
    booking = _object(_object(answer.get("result")).get("reservation"))
    return _Request(
        agent=agent,
        params=params,
        answer=answer,
        booking=booking,
        guest=_text(booking.get("guest_name")) or _text(params.get("guest_name")) or "A guest",
        party=_int(booking.get("party_size")) or _int(params.get("party_size")),
        starts=_instant(booking.get("starts_at"), tz) or _instant(params.get("starts_at"), tz),
    )


def _answered(read: _Request, intent: str) -> tuple[str, str] | None:
    """A request the merchant's agent served; None for an intent it doesn't word."""
    agent, guest, party = read.agent, read.guest, read.party
    match intent:
        case "AVAILABILITY":
            day = _day(read.params.get("date"))
            what = _text(read.params.get("time")) or "free times"
            title = f"{agent} checked {what}" + (f" for {_guests(party)}" if party else "")
            text = f"{short_day(day)}. " if day else ""
            return title, text + "Your agent offered the closest free times."
        case "BOOK":
            return f"{guest} booked through {agent}", _booking(read.starts, party, read.booking)
        case "AMEND":
            return f"{guest} changed their booking through {agent}", _booking(
                read.starts, party, read.booking
            )
        case "CANCEL":
            return f"{guest} cancelled through {agent}", _booking(read.starts, party, {})
    return None


def _refused(
    read: _Request, intent: str, status: int, max_party_size: int
) -> tuple[str, str] | None:
    """A request the merchant's agent refused; None for a status it doesn't word."""
    agent, party = read.agent, read.party
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
        case 409:
            return _conflict(read, intent)
        case 404:
            reference = _text(read.params.get("reservation_id"))
            known = f"Reference {reference}." if reference else "Nothing was changed."
            return f"{agent} asked about a booking you don't have", known
        case 503:
            return (
                f"{agent} was sent to your booking page",
                "Your agent was paused or couldn't answer, so the guest got your link instead.",
            )
        case 400:
            return f"{agent} sent a request your agent couldn't read", "Nothing was changed."
    return None


def _conflict(read: _Request, intent: str) -> tuple[str, str]:
    """A `409`: a booking that had started, no seats for more guests, or a time just gone."""
    guest = read.guest
    if intent == "CANCEL":
        return (
            f"{guest} tried to cancel a booking that had started",
            "Bookings can only be changed before they start.",
        )
    if intent == "AMEND" and _started(read.answer):
        return (
            f"{guest} tried to change a booking that had started",
            "Bookings can only be changed before they start.",
        )
    if intent == "AMEND" and read.params.get("starts_at") is None:
        # Same time, more guests: no table was free for them then.
        return f"{guest} asked for more seats than were free", "Their booking stays as it was."
    return f"{guest} asked for a time that had just gone", f"{read.agent} offered them another one."


def _booking(starts: datetime | None, party: int | None, booking: dict[str, Any]) -> str:
    parts = []
    if starts:
        parts += [short_day(starts.date()), hhmm(starts)]
    if party:
        parts.append(_guests(party))
    table = _text(booking.get("table_label"))
    if table:
        parts.append(f"table {table}")
    return ", ".join(parts) + "." if parts else ""


def _started(answer: dict[str, Any]) -> bool:
    message = _text(_object(answer.get("error")).get("message"))
    return message is not None and message.endswith(STARTED)


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
