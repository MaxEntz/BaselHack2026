"""The Agent page's titles and texts (#146), worded from the stored BATP frames."""

import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from agent.requests import describe
from core.errors import BookingStarted

TZ = ZoneInfo("Europe/Zurich")
SLUG = "la-graine-verte"
DEMO = Path(__file__).parents[1] / "fixtures/demo/la-graine-verte-management.json"


def frames(intent: str, params: dict[str, Any], answer: dict[str, Any]) -> tuple[str, str]:
    request = {"type": "request", "id": "r-1", "intent": intent, "merchant": SLUG, "params": params}
    response = {"type": "response", "id": "r-1"} | answer
    return json.dumps(request), json.dumps(response)


def word(
    intent: str, status: int, params: dict[str, Any], answer: dict[str, Any], limit: int = 4
) -> tuple[str, str]:
    request, response = frames(intent, params, {"status": status} | answer)
    return describe("Claude", intent, status, request, response, max_party_size=limit, tz=TZ)


BOOKED = {
    "result": {
        "reservation": {
            "id": "R-007",
            "merchant": SLUG,
            "starts_at": "2026-10-31T19:00:00+00:00",  # 20:00 in Basel
            "party_size": 3,
            "table_label": "T9",
            "status": "confirmed",
            "guest_name": "Emma Roth",
            "via": "claude",
        }
    }
}
BOOKING = {
    "starts_at": "2026-10-31T20:00:00+01:00",
    "party_size": 3,
    "guest_name": "Emma Roth",
    "idempotency_key": "c-7f3a",
}


def refusal(code: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": "No."}}


def test_the_issues_four_wordings() -> None:
    """The four rows of #146, as the mockup shows them."""
    checked = {"date": "2026-10-31", "time": "20:00", "party_size": 3}
    slots = {"result": {"slots": []}}

    assert word("AVAILABILITY", 200, checked, slots) == (
        "Claude checked 20:00 for 3 guests",
        "Sat 31 Oct. Your agent offered the closest free times.",
    )
    assert word("BOOK", 200, BOOKING, BOOKED) == (
        "Emma Roth booked through Claude",
        "Sat 31 Oct, 20:00, 3 guests, table T9.",
    )
    assert word("AVAILABILITY", 413, checked | {"party_size": 8}, refusal("party_too_large")) == (
        "A group of 8 was sent to your booking page",
        "Your limit is 4, so the guest got your link instead.",
    )
    assert word("BOOK", 409, BOOKING | {"guest_name": "Lucas Martin"}, refusal("x")) == (
        "Lucas Martin asked for a time that had just gone",
        "Claude offered them another one.",
    )


def test_the_demo_fixture_is_worded_as_the_server_words_it() -> None:
    """The web's mock serves these rows: the real server must say the same."""
    demo = json.loads(DEMO.read_text())
    limit = demo["rules"]["max_party_size"]

    for row in demo["agent_requests"]:
        worded = describe(
            row["agent_name"],
            row["intent"],
            row["status"],
            row["request"],
            row["response"],
            max_party_size=limit,
            tz=TZ,
        )
        assert worded == (row["title"], row["text"])


@pytest.mark.parametrize(
    ("intent", "status", "params", "answer", "expected"),
    [
        (
            "AVAILABILITY",
            200,
            {"date": "2026-11-02", "party_size": 1},
            {"result": {"slots": []}},
            (
                "Claude checked free times for 1 guest",
                "Mon 2 Nov. Your agent offered the closest free times.",
            ),
        ),
        (
            "AMEND",
            200,
            {"reservation_id": "R-007", "guest_name": "Emma Roth", "party_size": 3},
            BOOKED,
            (
                "Emma Roth changed their booking through Claude",
                "Sat 31 Oct, 20:00, 3 guests, table T9.",
            ),
        ),
        (
            "CANCEL",
            200,
            {"reservation_id": "R-007", "guest_name": "Emma Roth"},
            BOOKED,
            ("Emma Roth cancelled through Claude", "Sat 31 Oct, 20:00, 3 guests."),
        ),
        (
            "BOOK",
            413,
            BOOKING,  # 3 guests, within the limit: no table seats them
            refusal("party_too_large"),
            (
                "A group of 3 was sent to your booking page",
                "No table fits them, so the guest got your link instead.",
            ),
        ),
        (
            "CANCEL",
            409,
            {"reservation_id": "R-007", "guest_name": "Emma Roth"},
            refusal("slot_unavailable"),
            (
                "Emma Roth tried to cancel a booking that had started",
                "Bookings can only be changed before they start.",
            ),
        ),
        (
            "AMEND",
            409,
            {"reservation_id": "R-007", "guest_name": "Emma Roth", "party_size": 5},
            {"error": {"code": "slot_unavailable", "message": str(BookingStarted("R-007"))}},
            (
                "Emma Roth tried to change a booking that had started",
                "Bookings can only be changed before they start.",
            ),
        ),
        (
            "AMEND",
            409,
            {"reservation_id": "R-007", "guest_name": "Emma Roth", "party_size": 5},
            refusal("slot_unavailable"),
            ("Emma Roth asked for more seats than were free", "Their booking stays as it was."),
        ),
        (
            "AMEND",
            409,
            {"reservation_id": "R-007", "guest_name": "Emma Roth", "starts_at": None},
            refusal("slot_unavailable"),  # `null` from another client: still a resize
            ("Emma Roth asked for more seats than were free", "Their booking stays as it was."),
        ),
        (
            "AMEND",
            409,
            {
                "reservation_id": "R-007",
                "guest_name": "Emma Roth",
                "starts_at": BOOKING["starts_at"],
            },
            refusal("slot_unavailable"),
            ("Emma Roth asked for a time that had just gone", "Claude offered them another one."),
        ),
        (
            "AMEND",
            404,
            {"reservation_id": "R-404", "guest_name": "Emma Roth", "party_size": 2},
            refusal("not_found"),
            ("Claude asked about a booking you don't have", "Reference R-404."),
        ),
        (
            "BOOK",
            503,
            BOOKING,
            refusal("agent_unavailable"),
            (
                "Claude was sent to your booking page",
                "Your agent was paused or couldn't answer, so the guest got your link instead.",
            ),
        ),
        (
            "BOOK",
            400,
            {"party_size": "lots"},
            refusal("invalid_input"),
            ("Claude sent a request your agent couldn't read", "Nothing was changed."),
        ),
        ("BOOK", 429, BOOKING, refusal("too_many_requests"), ("Claude: book, answered 429", "")),
    ],
)
def test_other_answers(
    intent: str,
    status: int,
    params: dict[str, Any],
    answer: dict[str, Any],
    expected: tuple[str, str],
) -> None:
    assert word(intent, status, params, answer) == expected


@pytest.mark.parametrize(
    ("request_frame", "response_frame"),
    [("not json", "{}"), ("[]", "null"), ('{"params": "x"}', '{"result": 4}')],
)
def test_frames_it_cant_read_still_get_a_title(request_frame: str, response_frame: str) -> None:
    title, _ = describe(
        "Claude", "BOOK", 200, request_frame, response_frame, max_party_size=4, tz=TZ
    )

    assert title == "A guest booked through Claude"


# A turn with the merchant's agent (#253): from what code wrote, never from `reply`.

TALKED = {"conversation": "c-1", "reply": "THE MODEL'S TEXT", "tools_called": []}
QUESTION = {"message": "Do you have\nvegetarian dishes?", "idempotency_key": "k"}


def talked(**fields: Any) -> tuple[str, str]:
    return word("TALK", 200, QUESTION, {"result": TALKED | fields})


def test_a_question_quotes_what_the_guests_agent_asked() -> None:
    assert talked() == ("Claude asked your agent", '"Do you have vegetarian dishes?"')


def test_a_long_question_is_cut_to_fit_the_row() -> None:
    title, text = word("TALK", 200, {"message": "a" * 500}, {"result": TALKED})

    assert title == "Claude asked your agent"
    assert len(text) == 122
    assert text.endswith('…"')


def test_offered_times_are_said() -> None:
    slot = {"starts_at": "2026-10-31T19:00:00+00:00", "table_label": "T1"}

    assert talked(slots=[slot])[1] == (
        '"Do you have vegetarian dishes?" Your agent offered free times.'
    )


def test_a_proposal_waits_for_the_guests_yes() -> None:
    pending = {
        "id": "p-1",
        "action": "book",
        "summary": "Book a table for 2 on Sat 31 Oct at 20:00, under the name Emma Roth.",
        "expires_at": "2026-10-31T19:10:00+00:00",
    }

    assert talked(pending=pending) == (
        "Claude is waiting for its guest's yes",
        "Book a table for 2 on Sat 31 Oct at 20:00, under the name Emma Roth. "
        "Nothing changes before they say yes.",
    )


def test_a_booking_made_in_a_conversation_is_said_like_a_direct_one() -> None:
    booking = BOOKED["result"]["reservation"]

    assert talked(reservation=booking, tools_called=["create_reservation"]) == (
        "Emma Roth booked through Claude",
        "Sat 31 Oct, 20:00, 3 guests, table T9.",
    )


def test_a_change_made_in_a_conversation_is_said() -> None:
    booking = BOOKED["result"]["reservation"]

    assert talked(reservation=booking, tools_called=["update_reservation"])[0] == (
        "Emma Roth changed their booking through Claude"
    )


def test_a_cancellation_made_in_a_conversation_is_said() -> None:
    booking = BOOKED["result"]["reservation"] | {"status": "cancelled"}

    assert talked(reservation=booking, tools_called=["cancel_reservation"])[0] == (
        "Emma Roth cancelled through Claude"
    )


def test_an_answer_with_the_fallback_link_says_the_guest_got_it() -> None:
    assert talked(fallback_url="https://zinc.example/reservation") == (
        "Claude was sent to your booking page",
        '"Do you have vegetarian dishes?" The guest got your link.',
    )


def test_a_row_never_words_the_models_reply() -> None:
    cases = [talked(), talked(fallback_url="https://zinc.example/")]

    assert not [case for case in cases if "THE MODEL'S TEXT" in " ".join(case)]


def test_an_ended_conversation_is_not_a_booking_you_dont_have() -> None:
    answer = {"error": {"code": "not_found", "message": "Unknown conversation"}}

    assert word("TALK", 404, QUESTION, answer) == (
        "Claude wrote in a conversation that had ended",
        "Nothing was changed.",
    )
