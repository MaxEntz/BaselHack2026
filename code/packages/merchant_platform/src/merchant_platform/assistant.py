"""Platform assistant: OpenAI API tool-use loop over the admin tools."""

from collections.abc import Awaitable, Callable

from core.models import ChatEvent, TextEvent
from merchant_platform.sessions import Session

# Pushes one event into the merchant's `/chat` stream.
Emit = Callable[[ChatEvent], Awaitable[None]]
RunTurn = Callable[[Session, str, Emit], Awaitable[None]]


async def run_turn(session: Session, user_message: str, emit: Emit) -> None:
    """Answer one merchant message, streaming text, steps and cards through `emit`.

    Placeholder until the assistant lands (#17). The API ends every stream with `done`,
    whether or not this function emits it.
    """
    await emit(TextEvent(delta="The platform assistant isn't connected yet."))
