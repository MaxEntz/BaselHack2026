import pytest

from core.errors import (
    AgentError,
    AgentUnavailable,
    InvalidInput,
    NotFound,
    PartyTooLarge,
    SlotUnavailable,
)
from core.models import ErrorCode, ToolError


@pytest.mark.parametrize(
    ("exception", "code"),
    [
        (AgentUnavailable, "agent_unavailable"),
        (PartyTooLarge, "party_too_large"),
        (SlotUnavailable, "slot_unavailable"),
        (NotFound, "not_found"),
        (InvalidInput, "invalid_input"),
    ],
)
def test_business_errors_match_tool_error_codes(
    exception: type[AgentError], code: ErrorCode
) -> None:
    error = exception("Booking could not be completed")
    output = ToolError(error=error.code, message=str(error))

    assert output.model_dump(mode="json", exclude_none=True) == {
        "error": code,
        "message": "Booking could not be completed",
    }
