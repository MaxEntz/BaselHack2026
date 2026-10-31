"""The management site's routes, `/merchants/{slug}/…` (#129).

Their shapes are frozen in `core.models` and `web/src/types.ts`, so that the pages and the
backend are built in parallel. Each route answers `501` until the issue named in its message
implements it, after checking its input: a form that breaks a rule answers `422` with its
`problem` already. Errors use the `ManagementError` body. Every route needs the merchant's
owner (`auth.owner_of_path`): `401` without one, `403` for another merchant.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.management import ManagementService
from agent.service import AgentService
from builder.build import publish
from builder.site import load_site
from core.config import Settings
from core.errors import AgentUnavailable, NotFound
from core.forms import checked_rules, checked_services, checked_settings, checked_tables
from core.models import (
    AgentRequests,
    AgentStatusOutput,
    AgentTryInput,
    AgentTryOutput,
    AssistantConversation,
    BlockedDayInput,
    BlockedTimes,
    BookingDay,
    Dashboard,
    DaySlots,
    FloorPlanInput,
    FloorPlanOutput,
    GuestsOutput,
    GuestText,
    Hours,
    HoursInput,
    ManagedReservation,
    MerchantBookingInput,
    MerchantCancelOutput,
    MerchantOverview,
    MerchantSettings,
    MoveBookingInput,
    PublicSite,
    ReservationDay,
    Rules,
    RulesInput,
    SearchOutput,
    SearchText,
    SettingsInput,
    Slug,
    StepEvent,
)
from merchant_platform.accounts import Owner
from merchant_platform.agent_try import try_intent
from merchant_platform.auth import owner_of_path
from merchant_platform.overview import RegistryLookup, merchant_overview
from merchant_platform.sessions import SessionStore

router = APIRouter(
    prefix="/merchants/{slug}", tags=["management"], dependencies=[Depends(owner_of_path)]
)

SlugPath = Annotated[Slug, Path()]
Day = Annotated[BookingDay, Query()]
ReservationId = Annotated[GuestText, Path()]
Search = Annotated[SearchText, Query()]


class NotBuiltYet(Exception):
    """A frozen route whose issue hasn't landed: `501` with a `ManagementError` body. Every
    route is built now (#133 was the last); kept for the next frozen route."""

    def __init__(self, issue: str) -> None:
        super().__init__(f"Not built yet: see {issue}.")


def _agent(request: Request) -> AgentService:
    """Called in the handler, not as a dependency: a bad input must answer `422` first."""
    agent: AgentService | None = request.app.state.agent
    if agent is None:
        raise AgentUnavailable("The management site is not available yet.")
    return agent


@router.get("")
async def overview(
    request: Request, slug: SlugPath, owner: Annotated[Owner, Depends(owner_of_path)]
) -> MerchantOverview:
    registry: RegistryLookup | None = request.app.state.registry
    if registry is None:
        raise AgentUnavailable("The merchant registry isn't available.")
    record = await registry(slug)
    if record is None:
        raise NotFound(f"No merchant {slug!r}.")
    settings: Settings = request.app.state.settings
    return merchant_overview(record, owner.name, settings, datetime.now(UTC))


@router.get("/assistant")
async def assistant_conversation(
    request: Request, owner: Annotated[Owner, Depends(owner_of_path)]
) -> AssistantConversation:
    """The owner's conversation with the assistant (#140): the same on every device signed
    in with the owner's link, kept until the platform restarts."""
    sessions: SessionStore = request.app.state.sessions
    session = sessions.for_owner(owner.slug, owner.fingerprint or None)
    return AssistantConversation(session_id=session.id, items=tuple(session.items))


@router.post("/publish")
async def publish_agent(
    request: Request, slug: SlugPath, owner: Annotated[Owner, Depends(owner_of_path)]
) -> MerchantOverview:
    """Publishes the agent a build left waiting (#136), then answers its addresses."""
    engine: AsyncEngine | None = request.app.state.engine
    if engine is None:
        raise AgentUnavailable("Publishing isn't available on this platform.")

    async def unseen(_: StepEvent) -> None:
        """The chat shows the steps; this route answers the result."""

    await publish(slug, unseen, engine=engine)
    return await overview(request, slug, owner)


def bookings(request: Request) -> ManagementService | None:
    service: ManagementService | None = request.app.state.bookings
    return service


# Checked in the route, so that a bad request is still `invalid_input` first.
Bookings = Annotated[ManagementService | None, Depends(bookings)]


def need(service: ManagementService | None) -> ManagementService:
    """The management site's operations (#134, #135), or `agent_unavailable` without them."""
    if service is None:
        raise AgentUnavailable("Managing bookings isn't available on this platform.")
    return service


@router.get("/dashboard")
async def get_dashboard(
    slug: SlugPath, service: Bookings, date: Annotated[BookingDay | None, Query()] = None
) -> Dashboard:
    return await need(service).dashboard(slug, date)


@router.get("/reservations")
async def reservations(slug: SlugPath, date: Day, service: Bookings) -> ReservationDay:
    return await need(service).day(slug, date)


@router.post("/reservations", status_code=201)
async def add_booking(
    slug: SlugPath, body: MerchantBookingInput, service: Bookings
) -> ManagedReservation:
    return await need(service).add(slug, body)


@router.patch("/reservations/{reservation_id}")
async def move_booking(
    slug: SlugPath, reservation_id: ReservationId, body: MoveBookingInput, service: Bookings
) -> ManagedReservation:
    return await need(service).move(slug, reservation_id, body)


@router.post("/reservations/{reservation_id}/cancel")
async def cancel_booking(
    slug: SlugPath, reservation_id: ReservationId, service: Bookings
) -> MerchantCancelOutput:
    return await need(service).cancel(slug, reservation_id)


@router.get("/slots")
async def slots(
    slug: SlugPath,
    date: Day,
    party_size: Annotated[int, Query(gt=0, le=1000)],
    service: Bookings,
    ignore: Annotated[GuestText | None, Query()] = None,
) -> DaySlots:
    return await need(service).slots(slug, date, party_size, ignore)


@router.get("/guests")
async def guests(slug: SlugPath, request: Request, q: Search = "") -> GuestsOutput:
    return GuestsOutput(guests=tuple(await _agent(request).list_guests(slug, q)))


@router.get("/search")
async def search(slug: SlugPath, service: Bookings, q: Search = "") -> SearchOutput:
    return await need(service).search(slug, q)


@router.get("/hours")
async def hours(slug: SlugPath, request: Request) -> Hours:
    return Hours(services=(await _agent(request).get_policy(slug)).services)


@router.put("/hours")
async def save_hours(slug: SlugPath, body: HoursInput, request: Request) -> Hours:
    services = checked_services(body)
    policy = await _agent(request).update_policy(slug, services=services)
    return Hours(services=tuple(sorted(policy.services, key=lambda s: s.start)))


@router.get("/tables")
async def tables(slug: SlugPath, request: Request) -> FloorPlanOutput:
    return FloorPlanOutput(tables=tuple(await _agent(request).get_tables(slug)))


@router.put("/tables")
async def save_tables(slug: SlugPath, body: FloorPlanInput, request: Request) -> FloorPlanOutput:
    plan = checked_tables(body)
    return FloorPlanOutput(tables=tuple(await _agent(request).update_tables(slug, plan)))


@router.get("/rules")
async def rules(slug: SlugPath, request: Request) -> Rules:
    return await _agent(request).get_rules(slug)


@router.put("/rules")
async def save_rules(slug: SlugPath, body: RulesInput, request: Request) -> Rules:
    checked = checked_rules(body)
    return await _agent(request).update_rules(
        slug, checked.max_party_size, str(checked.fallback_url), checked.cancellation_policy
    )


@router.get("/settings")
async def merchant_settings(slug: SlugPath, request: Request) -> MerchantSettings:
    return await _agent(request).get_settings(slug)


@router.put("/settings")
async def save_settings(slug: SlugPath, body: SettingsInput, request: Request) -> MerchantSettings:
    checked = checked_settings(body)
    return await _agent(request).update_settings(
        slug, checked.name, checked.city, checked.price_level, checked.tags
    )


@router.get("/blocked")
async def blocked(slug: SlugPath, request: Request) -> BlockedTimes:
    return BlockedTimes(blocked=tuple(await _agent(request).list_blocked(slug)))


@router.put("/blocked")
async def set_blocked(
    slug: SlugPath, date: Day, body: BlockedDayInput, request: Request
) -> BlockedTimes:
    found = await _agent(request).set_blocked(slug, date, body.times)
    return BlockedTimes(blocked=tuple(found))


@router.post("/agent/pause")
async def pause_agent(slug: SlugPath, request: Request) -> AgentStatusOutput:
    """Guests' agents get the booking page; the website keeps booking."""
    await _agent(request).set_status(slug, "down")
    return AgentStatusOutput(status="down")


@router.post("/agent/resume")
async def resume_agent(slug: SlugPath, request: Request) -> AgentStatusOutput:
    await _agent(request).set_status(slug, "live")
    return AgentStatusOutput(status="live")


@router.get("/site")
async def public_site(request: Request, slug: SlugPath) -> PublicSite:
    engine: AsyncEngine | None = request.app.state.engine
    return await load_site(engine, slug) if engine else PublicSite(pages=())


@router.post("/agent/try")
async def try_agent(request: Request, slug: SlugPath, body: AgentTryInput) -> AgentTryOutput:
    settings: Settings = request.app.state.settings
    return await try_intent(settings, _agent(request), slug, body.intent)


@router.get("/agent/requests")
async def agent_requests(
    slug: SlugPath, request: Request, date: Day, refused: bool = False
) -> AgentRequests:
    found = await _agent(request).list_agent_requests(slug, date, refused=refused)
    return AgentRequests(requests=tuple(found))
