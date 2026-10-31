"""The management site's form rules (#129, #133), as `FormProblem`s the web can word.

The form bodies (`HoursInput`, `FloorPlanInput`, `RulesInput`, `SettingsInput`) are
structural, so these checks, not Pydantic, refuse them: each refusal names the rule and the
row. Rules that need the merchant's bookings or website (`table_in_use`, `table_too_small`,
`booking_page_off_site`) are the service's (#133).
"""

from itertools import combinations

from core.errors import FormProblem
from core.models import (
    MIN_SERVICE_MINUTES,
    FloorPlanInput,
    HoursInput,
    Problem,
    RulesInput,
    Service,
    SettingsInput,
    TableSpec,
)

# agent.admin.MAX_SEATS: the most seats a table, or a group, may have.
MAX_SEATS = 1000


def _minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def checked_services(body: HoursInput) -> tuple[Service, ...]:
    """The services of an opening-hours form, in the order sent."""
    if not body.services:
        raise FormProblem("Keep at least one service.", Problem(code="no_services"))
    services: list[Service] = []
    for index, draft in enumerate(body.services):
        if draft.start is None or draft.end is None:
            raise FormProblem(
                "A service needs a start and an end.",
                Problem(code="service_incomplete", services=(index,)),
            )
        if _minutes(draft.end) - _minutes(draft.start) < MIN_SERVICE_MINUTES:
            raise FormProblem(
                f"A service lasts at least {MIN_SERVICE_MINUTES} minutes.",
                Problem(code="service_too_short", services=(index,)),
            )
        services.append(Service(start=draft.start, end=draft.end))
    for (i, a), (j, b) in combinations(enumerate(services), 2):
        if a.start < b.end and b.start < a.end:
            raise FormProblem(
                "Services must not overlap.", Problem(code="services_overlap", services=(i, j))
            )
    return tuple(services)


def checked_tables(body: FloorPlanInput) -> tuple[TableSpec, ...]:
    """The tables of a floor-plan form, in the order sent."""
    if not body.tables:
        raise FormProblem("Keep at least one table.", Problem(code="no_tables"))
    seen: set[str] = set()
    for table in body.tables:
        if table.label in seen:
            raise FormProblem(
                "Table labels must be unique.", Problem(code="duplicate_table", table=table.label)
            )
        seen.add(table.label)
        if table.seats < 1:
            raise FormProblem(
                "Every table needs at least 1 seat.",
                Problem(code="table_without_seats", table=table.label),
            )
        if table.seats > MAX_SEATS:
            raise FormProblem(
                f"A table has at most {MAX_SEATS} seats.",
                Problem(code="too_many_seats", table=table.label, seats=MAX_SEATS),
            )
    return tuple(TableSpec(label=t.label, seats=t.seats) for t in body.tables)


def checked_rules(body: RulesInput) -> RulesInput:
    if body.max_party_size < 1:
        raise FormProblem("The largest group is at least 1.", Problem(code="group_too_small"))
    if body.max_party_size > MAX_SEATS:
        raise FormProblem(
            f"The largest group is at most {MAX_SEATS}.",
            Problem(code="too_many_seats", seats=MAX_SEATS),
        )
    return body.model_copy(update={"cancellation_policy": body.cancellation_policy or None})


def checked_settings(body: SettingsInput) -> SettingsInput:
    if not body.name:
        raise FormProblem("The restaurant needs a name.", Problem(code="name_required"))
    return body.model_copy(
        update={"city": body.city or None, "tags": tuple(dict.fromkeys(body.tags))}
    )
