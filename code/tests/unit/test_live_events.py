"""LiveBus fan-out and LISTEN/NOTIFY payload classification (no database)."""

import json
from pathlib import Path

import pytest

from core.models import LiveEvent
from merchant_platform.events import QUEUE_SIZE, LiveBus, classify

NOTIFICATION = Path(__file__).parents[2] / "web" / "src" / "mocks" / "notification.json"


def booking(number: int) -> LiveEvent:
    event = LiveEvent.model_validate(json.loads(NOTIFICATION.read_text()))
    row = event.reservation.model_copy(update={"id": f"R-{number:03d}"})
    return event.model_copy(update={"reservation": row})


def test_an_abandoned_subscriber_keeps_only_the_latest_events() -> None:
    bus = LiveBus()
    queue = bus.subscribe("le-petit-zinc")

    for number in range(QUEUE_SIZE + 5):
        bus.publish("le-petit-zinc", booking(number))

    assert queue.qsize() == QUEUE_SIZE
    assert queue.get_nowait().reservation.id == "R-005"


def test_unsubscribing_the_last_subscriber_forgets_the_merchant() -> None:
    bus = LiveBus()
    first, second = bus.subscribe("le-petit-zinc"), bus.subscribe("le-petit-zinc")

    bus.unsubscribe("le-petit-zinc", first)
    bus.publish("le-petit-zinc", booking(1))
    bus.unsubscribe("le-petit-zinc", second)
    bus.unsubscribe("le-petit-zinc", second)

    assert first.empty()
    assert second.qsize() == 1
    assert bus.subscriber_count("le-petit-zinc") == 0


@pytest.mark.parametrize(
    ("op", "status", "kind"),
    [
        ("INSERT", "confirmed", "new_booking"),
        ("INSERT", "cancelled", None),
        ("UPDATE", "confirmed", "updated"),
        ("UPDATE", "cancelled", "cancelled"),
    ],
)
def test_classify(op: str, status: str, kind: str | None) -> None:
    assert classify(op, status) == kind
