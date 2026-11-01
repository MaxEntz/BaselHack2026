"""Studio's Send buttons (#151): one real frame through the BATP node, as a test agent.

The platform opens a session of its own, so the merchant sees what any agent gets: a `404` until
they publish. BOOK makes a real booking under `TEST_GUEST`, like the assistant's
`send_test_booking`; AMEND and CANCEL act on that guest's latest booking here.
"""

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from agent.service import AgentService
from batp.client import BatpClient, parse_batp_url
from batp.codec import encode
from batp.messages import AgentIdentity, Intent
from core.config import Settings
from core.errors import AgentError, AgentUnavailable
from core.models import AgentTryOutput
from merchant_platform.trial import TEST_GUEST, TEST_PARTY, first_free_slot

# Needs an entry in the node's BATP_TOKENS when it has any, with this platform's BATP_TOKEN.
STUDIO_AGENT = AgentIdentity(
    id="urn:batp:agent:sesame-studio",
    name="Sésame Studio, trying your agent",
    kind="user-agent",
    operator="merchant-agents",
)
NO_NODE = "Sending frames needs the BATP node, and this platform has no BATP_URL."
# The first number no booking has: the node answers what it answers to any unknown booking.
NO_BOOKING = "R-000"
# Asked for when the merchant is hidden and has no slot to offer: the node refuses it anyway.
NOON = time(12)

Clock = Callable[[], datetime]


async def try_intent(
    settings: Settings,
    agent: AgentService,
    slug: str,
    intent: Intent,
    *,
    clock: Clock | None = None,
) -> AgentTryOutput:
    """The frames as sent and answered; the node's refusals are answers, not errors.

    `clock` returns the current time (tests inject a fixed one), whose local day the frames ask
    about.
    """
    if not settings.batp_url:
        raise AgentUnavailable(NO_NODE)
    host, port = parse_batp_url(settings.batp_url)
    token = settings.batp_token.get_secret_value() if settings.batp_token else None
    tz = ZoneInfo(settings.merchant_tz)
    now = clock() if clock is not None else datetime.now(tz)
    merchant, params = await _frame_for(agent, slug, intent, now.astimezone(tz).date(), tz)
    async with BatpClient(host, port, STUDIO_AGENT, token) as client:
        # Each press is its own session: a number would read `r-1` every time.
        request, response = await client.exchange(
            intent, merchant, params, request_id=f"r-{uuid4().hex[:6]}"
        )
    return AgentTryOutput(
        request=encode(request).decode().removesuffix("\n"),
        response=encode(response).decode().removesuffix("\n"),
        status=response.status,
    )


async def _frame_for(
    agent: AgentService, slug: str, intent: Intent, today: date, tz: ZoneInfo
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
