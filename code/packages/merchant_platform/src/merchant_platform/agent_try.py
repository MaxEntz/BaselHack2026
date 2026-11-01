"""Studio's Send buttons (#151) and message box (#253): one real frame through the BATP node, as
a test agent.

The platform opens a session of its own, so the merchant sees what any agent gets: a `404` until
they publish. BOOK makes a real booking under `TEST_GUEST`, like the assistant's
`send_test_booking`; AMEND and CANCEL act on that guest's latest booking here. A `TALK` has no
guest field: the merchant's agent asks for a name, as it would ask anyone, and a yes the owner
confirms makes a real booking under that name.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from agent.service import AgentService
from batp.client import BatpClient, parse_batp_url
from batp.codec import encode
from batp.messages import AgentIdentity, Intent
from core.config import Settings
from core.errors import AgentError, AgentUnavailable
from core.models import AgentTalkInput, AgentTryIntent, AgentTryOutput, TalkOutput
from merchant_platform.trial import TEST_GUEST, TEST_PARTY, first_free_slot

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
# The first number no booking has: the node answers what it answers to any unknown booking.
NO_BOOKING = "R-000"
# Asked for when the merchant is hidden and has no slot to offer: the node refuses it anyway.
NOON = time(12)

Clock = Callable[[], datetime]

log = logging.getLogger("merchant_platform.agent_try")


async def try_intent(
    settings: Settings,
    agent: AgentService,
    slug: str,
    intent: AgentTryIntent,
    *,
    clock: Clock | None = None,
) -> AgentTryOutput:
    """The frames as sent and answered; the node's refusals are answers, not errors.

    `clock` returns the current time (tests inject a fixed one), whose local day the frames ask
    about.
    """
    if not settings.batp_url:
        raise AgentUnavailable(NO_NODE)
    tz = ZoneInfo(settings.merchant_tz)
    now = clock() if clock is not None else datetime.now(tz)
    merchant, params = await _frame_for(agent, slug, intent, now.astimezone(tz).date(), tz)
    return await _exchange(settings, settings.batp_url, intent, merchant, params)


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


async def _frame_for(
    agent: AgentService, slug: str, intent: AgentTryIntent, today: date, tz: ZoneInfo
) -> tuple[str | None, dict[str, Any]]:
    match intent:
        case "PING":
            return None, {}
        case "DISCOVER":
            # The slug's words name the merchant, the way a guest would.
            return None, {"query": slug.replace("-", " ")}
        case "RECALL":
            return None, {"guest_name": TEST_GUEST}
        case "AVAILABILITY":
            return slug, {"date": today.isoformat(), "party_size": TEST_PARTY}
        case "BOOK":
            return slug, {
                "starts_at": (await _free_start(agent, slug, today, tz)).isoformat(),
                "party_size": TEST_PARTY,
                "guest_name": TEST_GUEST,
                "idempotency_key": f"studio-try-{uuid4()}",
            }
        case "AMEND":
            reservation_id, party = await _test_booking(agent, slug)
            return slug, {
                "reservation_id": reservation_id,
                "guest_name": TEST_GUEST,
                "party_size": 1 if party == TEST_PARTY else TEST_PARTY,
                "idempotency_key": f"studio-try-{uuid4()}",
            }
        case "CANCEL":
            reservation_id, _ = await _test_booking(agent, slug)
            return slug, {"reservation_id": reservation_id, "guest_name": TEST_GUEST}


async def _free_start(agent: AgentService, slug: str, today: date, tz: ZoneInfo) -> datetime:
    try:
        slot = await first_free_slot(agent, slug, today)
    except AgentError:
        slot = None  # Hidden from guests, or too small: the node gives the real answer.
    return slot.starts_at if slot else datetime.combine(today + timedelta(days=1), NOON, tz)


async def _test_booking(agent: AgentService, slug: str) -> tuple[str, int]:
    """The latest booking the test guest holds here that hasn't started, and its party."""
    reservations, _ = await agent.find_guest_reservations(TEST_GUEST)
    mine = [
        reservation
        for reservation in reservations
        if reservation.merchant == slug and reservation.status == "confirmed"
    ]
    if not mine:
        return NO_BOOKING, TEST_PARTY
    latest = max(mine, key=lambda reservation: reservation.starts_at)
    return latest.id, latest.party_size
