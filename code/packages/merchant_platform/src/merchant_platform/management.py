"""The management site's routes, `/merchants/{slug}/…` (#129).

Their shapes are frozen in `core.models` and `web/src/types.ts`, so that the pages and the
backend are built in parallel. Each route answers `501` until the issue named in its message
implements it; errors use the `ManagementError` body (a `ToolError`). The owner's session
check comes with #132.
"""

from typing import Annotated

from fastapi import APIRouter, Path, Query

from core.models import (
    MAX_GUEST_TEXT,
    AgentRequests,
    AgentStatusOutput,
    BlockedDayInput,
    BlockedTimes,
    BookingDay,
    Dashboard,
    DaySlots,
    FloorPlanOutput,
    GuestsOutput,
    Hours,
    HoursInput,
    ManagedReservation,
    MerchantBookingInput,
    MerchantCancelOutput,
    MerchantOverview,
    MerchantSettings,
    MoveBookingInput,
    ReservationDay,
    Rules,
    RulesInput,
    SearchOutput,
    SettingsInput,
    Slug,
    UpdateTablesInput,
)

router = APIRouter(prefix="/merchants/{slug}", tags=["management"])

SlugPath = Annotated[Slug, Path()]
Day = Annotated[BookingDay, Query()]
ReservationId = Annotated[str, Path(min_length=1, max_length=MAX_GUEST_TEXT)]
Search = Annotated[str, Query(max_length=MAX_GUEST_TEXT)]


class NotBuiltYet(Exception):
    """A frozen route whose issue hasn't landed: `501` with a `ManagementError` body."""

    def __init__(self, issue: str) -> None:
        super().__init__(f"Not built yet: see {issue}.")


@router.get("")
async def overview(slug: SlugPath) -> MerchantOverview:
    raise NotBuiltYet("#132")


@router.get("/dashboard")
async def dashboard(slug: SlugPath, date: Day) -> Dashboard:
    raise NotBuiltYet("#135")


@router.get("/reservations")
async def reservations(slug: SlugPath, date: Day) -> ReservationDay:
    raise NotBuiltYet("#134")


@router.post("/reservations", status_code=201)
async def add_booking(slug: SlugPath, body: MerchantBookingInput) -> ManagedReservation:
    raise NotBuiltYet("#134")


@router.patch("/reservations/{reservation_id}")
async def move_booking(
    slug: SlugPath, reservation_id: ReservationId, body: MoveBookingInput
) -> ManagedReservation:
    raise NotBuiltYet("#134")


@router.post("/reservations/{reservation_id}/cancel")
async def cancel_booking(slug: SlugPath, reservation_id: ReservationId) -> MerchantCancelOutput:
    raise NotBuiltYet("#134")


@router.get("/slots")
async def slots(
    slug: SlugPath,
    date: Day,
    party_size: Annotated[int, Query(gt=0)],
    ignore: Annotated[str | None, Query(max_length=MAX_GUEST_TEXT)] = None,
) -> DaySlots:
    raise NotBuiltYet("#134")


@router.get("/guests")
async def guests(slug: SlugPath, q: Search = "") -> GuestsOutput:
    raise NotBuiltYet("#144")


@router.get("/search")
async def search(slug: SlugPath, q: Search = "") -> SearchOutput:
    raise NotBuiltYet("#135")


@router.get("/hours")
async def hours(slug: SlugPath) -> Hours:
    raise NotBuiltYet("#133")


@router.put("/hours")
async def save_hours(slug: SlugPath, body: HoursInput) -> Hours:
    raise NotBuiltYet("#133")


@router.get("/tables")
async def tables(slug: SlugPath) -> FloorPlanOutput:
    raise NotBuiltYet("#133")


@router.put("/tables")
async def save_tables(slug: SlugPath, body: UpdateTablesInput) -> FloorPlanOutput:
    raise NotBuiltYet("#133")


@router.get("/rules")
async def rules(slug: SlugPath) -> Rules:
    raise NotBuiltYet("#133")


@router.put("/rules")
async def save_rules(slug: SlugPath, body: RulesInput) -> Rules:
    raise NotBuiltYet("#133")


@router.get("/settings")
async def merchant_settings(slug: SlugPath) -> MerchantSettings:
    raise NotBuiltYet("#133")


@router.put("/settings")
async def save_settings(slug: SlugPath, body: SettingsInput) -> MerchantSettings:
    raise NotBuiltYet("#133")


@router.get("/blocked")
async def blocked(slug: SlugPath) -> BlockedTimes:
    raise NotBuiltYet("#133")


@router.put("/blocked")
async def set_blocked(slug: SlugPath, date: Day, body: BlockedDayInput) -> BlockedTimes:
    raise NotBuiltYet("#133")


@router.post("/agent/pause")
async def pause_agent(slug: SlugPath) -> AgentStatusOutput:
    raise NotBuiltYet("#146")


@router.post("/agent/resume")
async def resume_agent(slug: SlugPath) -> AgentStatusOutput:
    raise NotBuiltYet("#146")


@router.get("/agent/requests")
async def agent_requests(slug: SlugPath, date: Day, refused: bool = False) -> AgentRequests:
    raise NotBuiltYet("#146")
