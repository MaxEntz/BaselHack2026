"""Postgres LISTEN/NOTIFY -> SSE live events for the merchant chat.

Every write on a merchant's `reservations` notifies the `reservations` channel with
identifiers only (trigger from migration 0001). `LiveListener` turns each notification
into a `LiveEvent` and `LiveBus` hands it to the merchant's open `/events` streams.

Live events report what guests do. The merchant's own changes (its cancellations, the
bookings it enters itself) are not sent: its chat already confirms them, and the
booking's channel would read as the guest's ("Booking cancelled via Claude", #81).
"""

import asyncio
import logging
import re
from collections import defaultdict
from datetime import date
from typing import Literal, NamedTuple
from zoneinfo import ZoneInfo

import psycopg
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from builder.slugs import MAX_SLUG, MIN_SLUG
from core.db import merchant_conn, merchants_t, reservations_t, tables_t
from core.models import AdminReservationRow, LiveEvent, ReservationStatus, Via

log = logging.getLogger("merchant_platform.events")

CHANNEL = "reservations"
# An abandoned tab must not make memory grow: beyond this, the oldest events are dropped.
QUEUE_SIZE = 100
BACKOFF_SECONDS = (1, 2, 4, 10)
# `schema_for` in reverse: `m_` + slug with `-` replaced by `_` (slugs have no `_`).
_MERCHANT_SCHEMA = re.compile(rf"^m_([a-z0-9_]{{{MIN_SLUG},{MAX_SLUG}}})$")


class LiveBus:
    """Fans live events out to the `/events` streams open for each merchant."""

    def __init__(self) -> None:
        self._subscribers: defaultdict[str, set[asyncio.Queue[LiveEvent]]] = defaultdict(set)

    def subscribe(self, slug: str) -> asyncio.Queue[LiveEvent]:
        queue: asyncio.Queue[LiveEvent] = asyncio.Queue(QUEUE_SIZE)
        self._subscribers[slug].add(queue)
        return queue

    def unsubscribe(self, slug: str, queue: asyncio.Queue[LiveEvent]) -> None:
        subscribers = self._subscribers.get(slug)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            del self._subscribers[slug]

    def subscriber_count(self, slug: str) -> int:
        return len(self._subscribers.get(slug, ()))

    def publish(self, slug: str, event: LiveEvent) -> None:
        for queue in self._subscribers.get(slug, ()):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)


class Notification(BaseModel):
    """`pg_notify` payload: identifiers only (NOTIFY payloads are limited to 8000 bytes)."""

    # `schema` would shadow a BaseModel attribute.
    schema_: str = Field(alias="schema")
    op: Literal["INSERT", "UPDATE"]
    id: str
    status: ReservationStatus
    via: Via
    # `merchant` when a merchant operation made the change (`core.db.as_merchant`, #134).
    actor: str | None = None


Kind = Literal["new_booking", "updated", "cancelled"]


class Booking(NamedTuple):
    """A reservation as re-read for a live event."""

    row: AdminReservationRow
    day: date
    cancelled_by: Literal["guest", "merchant"] | None


def classify(op: str, status: str) -> Kind | None:
    if op == "INSERT":
        return "new_booking" if status == "confirmed" else None
    return "cancelled" if status == "cancelled" else "updated"


def made_by_merchant(kind: Kind, booking: Booking, actor: str | None = None) -> bool:
    """The merchant's own change, which its pages and chat already confirm.

    A merchant operation says so (`actor`, #134): a move from the management site. Without it,
    the booking tells: a cancellation by the merchant, or a booking it entered itself.
    """
    if actor == "merchant":
        return True
    if kind == "cancelled":
        return booking.cancelled_by == "merchant"
    return kind == "new_booking" and booking.row.via == "merchant"


class LiveListener:
    """LISTENs on a dedicated connection and publishes each reservation change to the bus.

    The LISTEN connection does nothing else: reservations are re-read through `engine`.
    """

    def __init__(self, database_url: str, engine: AsyncEngine, bus: LiveBus, tz: str) -> None:
        # SQLAlchemy URL (`postgresql+psycopg://…`) → plain libpq URL for psycopg.
        self._conninfo = (
            make_url(database_url)
            .set(drivername="postgresql")
            .render_as_string(hide_password=False)
        )
        self._engine = engine
        self._bus = bus
        self._tz = ZoneInfo(tz)
        self._merchants: set[str] = set()
        self.listening = asyncio.Event()

    async def run(self) -> None:
        """Listen until cancelled, reconnecting with an increasing backoff."""
        failures = 0
        while True:
            reason = "closed"
            try:
                async with await psycopg.AsyncConnection.connect(
                    self._conninfo, autocommit=True
                ) as conn:
                    await conn.execute(f"LISTEN {CHANNEL}")
                    failures = 0
                    self.listening.set()
                    log.info("live_listener_connected")
                    async for notify in conn.notifies():
                        await self.handle(notify.payload)
            except psycopg.Error as error:
                reason = type(error).__name__
            except Exception:
                # Keep listening whatever happens: a dead listener means no live events for
                # the rest of the demo. (CancelledError isn't an Exception: shutdown works.)
                log.exception("live_listener_crashed")
                reason = "crash"
            self.listening.clear()
            delay = BACKOFF_SECONDS[min(failures, len(BACKOFF_SECONDS) - 1)]
            failures += 1
            log.warning("live_listener_disconnected", extra={"error": reason, "retry_in_s": delay})
            await asyncio.sleep(delay)

    async def handle(self, payload: str) -> None:
        """Publish one notification; a bad one is logged and skipped, never fatal."""
        try:
            notification = Notification.model_validate_json(payload)
        except ValidationError:
            log.warning("live_event_malformed")
            return
        kind = classify(notification.op, notification.status)
        if kind is None:
            return
        try:
            slug = await self._merchant(notification.schema_)
            if slug is None:
                log.warning("live_event_unknown_schema", extra={"schema": notification.schema_})
                return
            booking = await self._reservation(slug, notification.id)
        except Exception:
            log.exception("live_event_lookup_failed", extra={"schema": notification.schema_})
            return
        if booking is None:
            return
        row = booking.row
        if made_by_merchant(kind, booking, notification.actor):
            log.info("live_event_skipped", extra={"merchant": slug, "kind": kind, "id": row.id})
            return
        event = LiveEvent(kind=kind, via=notification.via, date=booking.day, reservation=row)
        self._bus.publish(slug, event)
        log.info("live_event", extra={"merchant": slug, "kind": kind, "id": row.id})

    async def _merchant(self, schema: str) -> str | None:
        """The slug behind `schema` if it belongs to a registered merchant.

        Merchants provisioned after start-up are looked up on their first event, then cached.
        """
        match = _MERCHANT_SCHEMA.match(schema)
        if match is None:
            return None
        slug = match.group(1).replace("_", "-")
        if slug in self._merchants:
            return slug
        async with self._engine.connect() as conn:
            known = await conn.scalar(select(merchants_t.c.slug).where(merchants_t.c.slug == slug))
        if known is None:
            return None
        self._merchants.add(slug)
        return slug

    async def _reservation(self, slug: str, reservation_id: str) -> Booking | None:
        query = (
            select(
                reservations_t.c.id,
                func.lower(reservations_t.c.during).label("starts_at"),
                reservations_t.c.guest_name,
                reservations_t.c.party_size,
                tables_t.c.label,
                reservations_t.c.via,
                reservations_t.c.status,
                reservations_t.c.cancelled_by,
            )
            .join(tables_t, tables_t.c.id == reservations_t.c.table_id)
            .where(reservations_t.c.id == reservation_id)
        )
        async with merchant_conn(self._engine, slug) as conn:
            row = (await conn.execute(query)).one_or_none()
        if row is None:
            return None
        start = row.starts_at.astimezone(self._tz)
        reservation = AdminReservationRow(
            id=row.id,
            time=start.strftime("%H:%M"),
            guest_name=row.guest_name,
            party_size=row.party_size,
            table_label=row.label,
            via=row.via,
            status=row.status,
        )
        return Booking(reservation, start.date(), row.cancelled_by)
