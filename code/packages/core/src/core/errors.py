"""Business errors; the gateway adds fallback URLs when serializing them."""

from typing import ClassVar

from core.models import ErrorCode, Problem


class AgentError(Exception):
    code: ClassVar[ErrorCode]

    def __init__(self, message: str) -> None:
        super().__init__(message)


class PartyTooLarge(AgentError):
    code: ClassVar[ErrorCode] = "party_too_large"


class SlotUnavailable(AgentError):
    code: ClassVar[ErrorCode] = "slot_unavailable"


# The end of `BookingStarted`'s message: the request log words the refusal from it (#188).
STARTED = "has already started"


class BookingStarted(SlotUnavailable):
    """A change or cancellation of a booking that has already started (#79)."""

    def __init__(self, reservation_id: str) -> None:
        super().__init__(f"Booking {reservation_id} {STARTED}")


class NotFound(AgentError):
    code: ClassVar[ErrorCode] = "not_found"


class InvalidInput(AgentError):
    code: ClassVar[ErrorCode] = "invalid_input"


class AgentUnavailable(AgentError):
    code: ClassVar[ErrorCode] = "agent_unavailable"


class FormProblem(InvalidInput):
    """A management form broke a rule the web words itself (#133): `422` with `problem`."""

    def __init__(self, message: str, problem: Problem) -> None:
        super().__init__(message)
        self.problem = problem
