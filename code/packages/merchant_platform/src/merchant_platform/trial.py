"""The test guest's bookings (#151, #152): what the assistant's `send_test_booking` and
Studio's Send buttons book as an agent would, so that the merchant sees one arrive."""

from datetime import date, timedelta

from agent.service import AgentService
from core.models import Slot

TEST_GUEST = "Test booking"
TEST_PARTY = 2


async def first_free_slot(agent: AgentService, slug: str, today: date) -> Slot | None:
    """The first time free for `TEST_PARTY` today, else tomorrow; None when neither has one.
    The agent's errors (a hidden merchant, a party too large) go to the caller."""
    for day in (today, today + timedelta(days=1)):
        slots = await agent.get_availability(slug, day, TEST_PARTY)
        if slots:
            return slots[0]
    return None
