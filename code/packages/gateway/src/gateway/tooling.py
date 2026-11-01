"""What the shared endpoint's tools and each merchant's own have in common (#23, #220, #254).

Both MCP servers expose `talk_to_merchant`, the one way to book: the same hints, argument types,
rule for writes and body (`Tools`). They differ only in where the merchant comes from (an
argument, or the path) and in the sentence that opens its description. Claude reads these texts:
they are a contract (#14, #109), so a change here changes what both endpoints tell it.
"""

import textwrap
from collections.abc import Awaitable, Callable
from datetime import date
from functools import partial
from typing import Annotated
from uuid import UUID

from mcp.types import ToolAnnotations
from pydantic import AfterValidator, Field
from sqlalchemy.ext.asyncio import AsyncEngine

from agent import discovery
from core.config import Settings
from core.models import (
    GetMyReservationsOutput,
    GuestText,
    OpaqueId,
    SearchFilters,
    SearchMerchantsOutput,
    TalkInput,
    TalkMessage,
    TalkOutput,
    ToolError,
)
from gateway.agent_client import AgentClient
from gateway.fallback import TALK_TOOL, call_merchant, call_service

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
# A conversation can end in a cancellation, once the user said yes; a key makes a resend harmless.
CONVERSATION = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
)

GuestName = Annotated[
    GuestText, Field(description="Guest's full name, the same one for the whole booking.")
]
Message = Annotated[
    TalkMessage,
    Field(
        description=(
            "What the user wants, in natural language, as they would say it to the restaurant, "
            "with their known preferences."
        ),
    ),
]


# A random UUID, not free text: every Claude user reaches the node under the gateway's one agent
# id, and the node replays a key's stored answer. Two guests sending the same first message with
# the same key would share a conversation and its proposal (#251, review of #264). A version 4
# UUID is random; the examples a model may copy instead (the nil UUID, RFC 4122's
# `123e4567-e89b-12d3-…`, a version 1) are not.
def _random(key: UUID) -> UUID:
    if key.version != 4:
        raise ValueError("generate a random (version 4) UUID")
    return key


TalkKey = Annotated[
    UUID,
    AfterValidator(_random),
    Field(
        description=(
            "A fresh random (version 4) UUID you generate per message; reuse it only to resend "
            "the same message."
        )
    ),
]
Conversation = Annotated[
    OpaqueId | None,
    Field(description="The `conversation` of the agent's last answer; omit it to start one."),
]
Confirm = Annotated[
    OpaqueId | None,
    Field(
        description=(
            "The `pending.id` the user said an explicit yes to, with that answer's `conversation`."
        )
    ),
]

# What a yes isn't (#14, #109): `{chosen}` is what the user picks from the agent's answers.
NOT_A_YES = (
    "Choosing {chosen} is not a yes, even when the request looks complete. Nor is a "
    'confirmation given before the summary ("I confirm in advance", "book directly"): show the '
    "summary anyway."
)
# The rule for writes in the server instructions (#14, #109, #251): the agent proposes, as
# `pending`, and only a `confirm` sent after the user's yes to its summary lets it write.
TALK_CONFIRM_RULE = (
    "When talk_to_merchant returns `pending`, show the user its `summary`, ask them to confirm, "
    "and stop: send its `id` as `confirm` only once the user's reply to that summary is an "
    f"explicit yes. {NOT_A_YES} So never send `confirm` in the same reply as the "
    "talk_to_merchant call that returned the `pending`. If the agent then answers that the "
    "confirmed time is gone, don't confirm another one: relay its new times and ask again."
)
# Instructions are bullets of at most this many characters a line.
INSTRUCTIONS_WIDTH = 98

GIVE_FALLBACK = "If the result contains `fallback_url`, give it to the user."
# What `talk_to_merchant` says on both endpoints, after each one's opening sentence.
TALK_RULES = (
    "Write `message` as the user would say it to the restaurant, with what they want and their "
    "known preferences (dietary needs, terrace, kid-friendly, budget). Relay `reply` to the user, "
    "and only propose times the agent offered. To go on with the same agent, pass back the "
    "`conversation` of its last answer. When the result contains `pending`, show the user its "
    "`summary`, ask them to confirm, and stop. Send `confirm` (that `pending.id`) only once their "
    "reply to that summary is an explicit yes, even if they confirmed in advance, and never in "
    "the same reply as the call that returned the `pending`. Generate a fresh random UUID as "
    "`idempotency_key` per message and reuse it only to resend the same message. "
    f"{GIVE_FALLBACK}"
)


def bullet(text: str) -> str:
    """`text` as a bullet of the server instructions."""
    return textwrap.fill(
        text, width=INSTRUCTIONS_WIDTH, initial_indent="- ", subsequent_indent="  "
    )


def talk_confirm_rule(chosen: str) -> str:
    """`TALK_CONFIRM_RULE` as a bullet of the server instructions."""
    return bullet(TALK_CONFIRM_RULE.format(chosen=chosen))


async def my_reservations(agent: AgentClient, guest_name: str) -> GetMyReservationsOutput:
    """`get_my_reservations`: the guest's upcoming bookings and notices."""
    reservations, notices = await agent.get_my_reservations(guest_name)
    return GetMyReservationsOutput(reservations=tuple(reservations), notices=tuple(notices))


async def talk(
    agent: AgentClient,
    *,
    merchant: str,
    message: str,
    idempotency_key: UUID,
    conversation: str | None,
    confirm: str | None,
) -> TalkOutput:
    """`talk_to_merchant`: the agent's answer, once the contract has checked the message. The key
    travels in its canonical form, so that one UUID written two ways stays one key."""
    turn = TalkInput(
        merchant=merchant,
        message=message,
        idempotency_key=str(idempotency_key),
        conversation=conversation,
        confirm=confirm,
    )
    return await agent.talk(turn)


class Tools:
    """What the user tools do once the SDK has checked their arguments; they never raise.

    A talk is routed to its merchant (`fallback.call_merchant`): the shared endpoint names it in
    an argument, a merchant's own endpoint in its path, with the deadline that endpoint's check
    started (#92). Its strings take keywords only, so that no call can swap them.
    """

    def __init__(self, agent: AgentClient, engine: AsyncEngine, settings: Settings) -> None:
        self.agent = agent
        self.engine = engine
        self.settings = settings

    async def search_merchants(
        self, query: str, near: str | None, day: date | None, filters: SearchFilters | None
    ) -> SearchMerchantsOutput | ToolError:
        """The search, once `discovery.check_search` let it through; when the service can't
        answer, the link of the merchant the query names, if any."""

        async def op() -> SearchMerchantsOutput:
            await discovery.check_search(query, filters, engine=self.engine)
            return await self.agent.search_merchants(query, near, day, filters)

        named = partial(discovery.find_named_merchant, query, engine=self.engine)
        return await call_service("search_merchants", op, settings=self.settings, named=named)

    async def get_my_reservations(self, guest_name: str) -> GetMyReservationsOutput | ToolError:
        op = partial(my_reservations, self.agent, guest_name)
        return await call_service("get_my_reservations", op, settings=self.settings)

    async def talk_to_merchant(
        self,
        slug: str,
        *,
        message: str,
        idempotency_key: UUID,
        conversation: str | None,
        confirm: str | None,
        deadline: float | None = None,
    ) -> TalkOutput | ToolError:
        """A turn of the conversation; only one carrying a `confirm` may have written."""
        op = partial(
            talk,
            self.agent,
            merchant=slug,
            message=message,
            idempotency_key=idempotency_key,
            conversation=conversation,
            confirm=confirm,
        )
        return await self._routed(TALK_TOOL, slug, op, deadline, writes=confirm is not None)

    async def _routed[T](
        self,
        tool: str,
        slug: str,
        op: Callable[[], Awaitable[T]],
        deadline: float | None,
        *,
        writes: bool = False,
    ) -> T | ToolError:
        find = partial(discovery.find_merchant, slug, engine=self.engine)
        return await call_merchant(
            slug, tool, find, op, settings=self.settings, deadline=deadline, writes=writes
        )
