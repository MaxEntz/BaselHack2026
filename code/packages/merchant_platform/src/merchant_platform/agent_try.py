"""Studio's Send buttons (#151) and message box (#253): one real frame through the BATP node, as
a test agent.

The platform opens a session of its own, so the merchant sees what any agent gets: a `404` until
they publish. The buttons send the intents that address no merchant (`DISCOVER`, `RECALL`,
`PING`); booking goes through the message box, since only the merchant's agent calls the booking
tools (#254). A `TALK` has no guest field: the merchant's agent asks for a name, as it would ask
anyone, and a yes the owner confirms makes a real booking under that name.
"""

import asyncio
import logging
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from batp.client import BatpClient, parse_batp_url
from batp.codec import encode
from batp.messages import AgentIdentity, Intent
from core.config import Settings
from core.errors import AgentUnavailable
from core.models import AgentTalkInput, AgentTryIntent, AgentTryOutput, TalkOutput
from merchant_platform.trial import TEST_GUEST

# Needs an entry in the node's BATP_TOKENS when it has any, with this platform's BATP_TOKEN.
STUDIO_AGENT = AgentIdentity(
    id="urn:batp:agent:sesame-studio",
    name="Sésame Studio, trying your agent",
    kind="user-agent",
    operator="merchant-agents",
)
NO_NODE = "Sending frames needs the BATP node, and this platform has no BATP_URL."
NO_ANSWER = "Your agent took too long to answer. Try again."
# The node gives a model turn 25 s (PROTOCOL.md): this only covers a node that never answers.
TALK_WAIT_S = 30

log = logging.getLogger("merchant_platform.agent_try")


async def try_intent(settings: Settings, slug: str, intent: AgentTryIntent) -> AgentTryOutput:
    """The frames as sent and answered; the node's refusals are answers, not errors."""
    if not settings.batp_url:
        raise AgentUnavailable(NO_NODE)
    params = _params_for(slug, intent)
    return await _exchange(settings, settings.batp_url, intent, None, params)


async def try_talk(settings: Settings, slug: str, body: AgentTalkInput) -> AgentTryOutput:
    """One message to the merchant's agent, with its answer read from the frame for the page."""
    if not settings.batp_url:
        raise AgentUnavailable(NO_NODE)
    params: dict[str, Any] = {
        "message": body.message,
        "idempotency_key": f"studio-try-{uuid4()}",
    }
    if body.conversation:
        params["conversation"] = body.conversation
    if body.confirm:
        params["confirm"] = body.confirm
    try:
        async with asyncio.timeout(TALK_WAIT_S):
            return await _exchange(settings, settings.batp_url, "TALK", slug, params)
    except TimeoutError:
        raise AgentUnavailable(NO_ANSWER) from None


async def _exchange(
    settings: Settings,
    batp_url: str,
    intent: Intent,
    merchant: str | None,
    params: dict[str, Any],
) -> AgentTryOutput:
    host, port = parse_batp_url(batp_url)
    token = settings.batp_token.get_secret_value() if settings.batp_token else None
    async with BatpClient(host, port, STUDIO_AGENT, token) as client:
        # Each press is its own session: a number would read `r-1` every time.
        request, response = await client.exchange(
            intent, merchant, params, request_id=f"r-{uuid4().hex[:6]}"
        )
    talk = _talk(response.result) if intent == "TALK" and response.status == 200 else None
    return AgentTryOutput(
        request=encode(request).decode().removesuffix("\n"),
        response=encode(response).decode().removesuffix("\n"),
        status=response.status,
        talk=talk,
    )


def _talk(result: dict[str, Any] | None) -> TalkOutput | None:
    """The answer for the page, or `None` when it isn't a `TalkOutput`: the frames still show
    what the agent sent, a booking it made included, rather than the route failing."""
    try:
        return TalkOutput.model_validate(result)
    except ValidationError as error:
        fields = sorted({".".join(map(str, e["loc"]))[:60] for e in error.errors()})
        log.warning("talk_answer_off_contract", extra={"fields": fields[:5]})
        return None


def _params_for(slug: str, intent: AgentTryIntent) -> dict[str, Any]:
    """The params of a button's frame; none of these intents addresses a merchant."""
    match intent:
        case "PING":
            return {}
        case "DISCOVER":
            # The slug's words name the merchant, the way a guest would.
            return {"query": slug.replace("-", " ")}
        case "RECALL":
            return {"guest_name": TEST_GUEST}
