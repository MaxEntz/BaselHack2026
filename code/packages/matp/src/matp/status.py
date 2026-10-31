"""Business errors ↔ MATP status codes (PROTOCOL.md §8, AGENTS.md §8.4)."""

from core.errors import (
    AgentError,
    AgentUnavailable,
    InvalidInput,
    NotFound,
    PartyTooLarge,
    SlotUnavailable,
)
from matp.messages import ErrorBody, Response

OK = 200
STATUS_BY_CODE = {
    "invalid_input": 400,
    "not_found": 404,
    "slot_unavailable": 409,
    "party_too_large": 413,
    "agent_unavailable": 503,
}
ERROR_BY_CODE: dict[str, type[AgentError]] = {
    error.code: error
    for error in (InvalidInput, NotFound, SlotUnavailable, PartyTooLarge, AgentUnavailable)
}
# Only these send the guest back to the merchant's own booking page.
WITH_FALLBACK = frozenset({"party_too_large", "agent_unavailable"})
# Error messages may echo input: keep them short.
MAX_MESSAGE = 300


class Refused(Exception):
    """A business error together with the merchant's fallback URL, raised by a node backend."""

    def __init__(self, error: AgentError, fallback_url: str | None) -> None:
        super().__init__(str(error))
        self.error = error
        self.fallback_url = fallback_url


def status_for(error: AgentError) -> int:
    return STATUS_BY_CODE[error.code]


def error_body(error: AgentError, fallback_url: str | None = None) -> ErrorBody:
    link = fallback_url if error.code in WITH_FALLBACK else None
    return ErrorBody(code=error.code, message=str(error)[:MAX_MESSAGE], fallback_url=link)


def raise_for(response: Response) -> None:
    """Raise the business error a response carries; anything else is `AgentUnavailable`."""
    if response.status == OK:
        return
    body = response.error
    if body is not None and body.code is not None:
        raise ERROR_BY_CODE[body.code](body.message)
    raise AgentUnavailable(body.message if body else f"MATP status {response.status}")
