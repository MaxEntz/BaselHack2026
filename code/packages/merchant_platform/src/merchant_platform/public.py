"""The routes anyone may call: `GET /health` and the showcase site's booking form,
`POST /public/{slug}/reservations` (CORS-limited to PUBLIC_CORS_ORIGINS)."""

import logging
import time
from typing import Annotated

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, ConfigDict, PositiveInt

from agent.service import AgentService
from core.errors import AgentError, AgentUnavailable
from core.models import BookingTime, GuestText, ReservationOutput, Slug

# The platform API's logger: these lines kept their name when the routes moved here.
log = logging.getLogger("merchant_platform.api")

router = APIRouter(tags=["public"])


class PublicReservationInput(BaseModel):
    """The showcase site's booking form (the merchant comes from the URL)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    starts_at: BookingTime
    party_size: PositiveInt
    guest_name: GuestText
    idempotency_key: GuestText


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/public/{slug}/reservations", status_code=201)
async def public_reservation(
    request: Request, slug: Annotated[Slug, Path()], body: PublicReservationInput
) -> ReservationOutput:
    agent: AgentService | None = request.app.state.agent
    if agent is None:
        raise AgentUnavailable("Online booking is not available yet.")
    started = time.perf_counter()
    # `ok` only once booked: a crash or a cancelled request is never logged as `ok` (#79).
    outcome = "error"
    try:
        reservation = await agent.create_reservation(
            slug,
            body.starts_at,
            body.party_size,
            body.guest_name,
            body.idempotency_key,
            via="website",
        )
        outcome = "ok"
    except AgentError as error:
        outcome = error.code
        raise
    finally:
        latency_ms = round((time.perf_counter() - started) * 1000)
        log.info(
            "public_reservation",
            extra={"merchant": slug, "latency_ms": latency_ms, "outcome": outcome},
        )
    return ReservationOutput(reservation=reservation)
