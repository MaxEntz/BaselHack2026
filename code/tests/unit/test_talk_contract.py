"""The `TALK` contract: what a guest's agent sends and gets back (PROTOCOL.md §7, #245)."""

from typing import Any

import pytest
from pydantic import ValidationError

from core.models import MAX_TALK_TEXT, PendingAction, TalkInput, TalkOutput

FIRST = {"merchant": "le-petit-zinc", "message": "Table for 4 tonight?", "idempotency_key": "m-1"}


def test_a_first_message_needs_no_conversation() -> None:
    talk = TalkInput.model_validate(FIRST)

    assert (talk.conversation, talk.confirm) == (None, None)


def test_a_message_may_span_lines_and_is_stripped() -> None:
    talk = TalkInput.model_validate(FIRST | {"message": "  Table for 4?\nVegetarian dishes?  "})

    assert talk.message == "Table for 4?\nVegetarian dishes?"


@pytest.mark.parametrize(
    "message",
    ["", "   ", "a" * (MAX_TALK_TEXT + 1), "tab\there", "nul\x00", "cr\rhere", "esc\x1b[2J"],
    ids=["empty", "blank", "too-long", "tab", "nul", "cr", "escape"],
)
def test_a_message_is_bounded_and_has_no_control_character_but_newline(message: str) -> None:
    with pytest.raises(ValidationError, match="message"):
        TalkInput.model_validate(FIRST | {"message": message})


def test_a_message_at_the_limit_is_accepted() -> None:
    assert len(TalkInput.model_validate(FIRST | {"message": "a" * MAX_TALK_TEXT}).message) == 2000


@pytest.mark.parametrize("missing", ["merchant", "message", "idempotency_key"])
def test_required_params(missing: str) -> None:
    with pytest.raises(ValidationError, match=missing):
        TalkInput.model_validate({k: v for k, v in FIRST.items() if k != missing})


def test_confirm_needs_the_conversation_of_its_proposal() -> None:
    with pytest.raises(ValidationError, match="confirm needs the conversation"):
        TalkInput.model_validate(FIRST | {"confirm": "p-1"})

    talk = TalkInput.model_validate(FIRST | {"conversation": "c-9f2c", "confirm": "p-1"})
    assert talk.confirm == "p-1"


@pytest.mark.parametrize("value", ["", "has space", "x" * 65, "c/../1", "é"])
def test_ids_are_opaque_tokens(value: str) -> None:
    with pytest.raises(ValidationError, match="conversation"):
        TalkInput.model_validate(FIRST | {"conversation": value})


def test_unknown_params_are_refused() -> None:
    # BATP params are strict (§6): a caller only sends its version's params.
    with pytest.raises(ValidationError, match="party_size"):
        TalkInput.model_validate(FIRST | {"party_size": 4})


ANSWER: dict[str, Any] = {
    "conversation": "c-9f2c",
    "reply": "Yes, two vegetarian mains tonight. I can do 20:00 or 20:30. Which name?",
    "slots": [{"starts_at": "2026-11-01T20:00:00+01:00", "table_label": "T5"}],
    "pending": None,
    "reservation": None,
    "fallback_url": None,
    "tools_called": ["get_availability"],
}


def test_the_answer_of_the_issue_round_trips() -> None:
    answer = TalkOutput.model_validate(ANSWER)

    assert answer.model_dump(mode="json") == ANSWER


def test_an_answer_needs_only_the_conversation_and_the_reply() -> None:
    answer = TalkOutput(conversation="c-1", reply="We're full tonight.")

    assert (answer.slots, answer.pending, answer.tools_called) == ((), None, ())


def test_a_proposal_is_an_action_with_its_summary_and_expiry() -> None:
    pending = {
        "id": "p-1",
        "action": "book",
        "summary": "Table for 4, Sunday 1 November at 20:00, name Ada",
        "expires_at": "2026-11-01T19:10:00+01:00",
    }
    answer = TalkOutput.model_validate(ANSWER | {"pending": pending})

    assert answer.pending == PendingAction.model_validate(pending)
    with pytest.raises(ValidationError, match="action"):
        PendingAction.model_validate(pending | {"action": "pay"})
    with pytest.raises(ValidationError, match="expires_at"):
        PendingAction.model_validate(pending | {"expires_at": "2026-11-01T19:10:00"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reply", "a" * (MAX_TALK_TEXT + 1)),
        ("tools_called", ["Get Availability"]),
        ("fallback_url", "not a url"),
    ],
)
def test_the_answer_is_bounded(field: str, value: object) -> None:
    with pytest.raises(ValidationError, match=field):
        TalkOutput.model_validate(ANSWER | {field: value})
