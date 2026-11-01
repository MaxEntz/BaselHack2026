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
from core.time_of_day import to_minutes

# The most seats a table, or a group, may have, well inside Postgres `integer`: the forms'
# bound here, and the tools' in `agent.admin`, so a typo never reaches the database.
MAX_SEATS = 1000


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
        if to_minutes(draft.end) - to_minutes(draft.start) < MIN_SERVICE_MINUTES:
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


def party_limit_problem(max_party_size: int) -> FormProblem | None:
    """The form's refusal of a group limit outside 1 to `MAX_SEATS`, naming the bound it
    breaks; None within bounds. The only check of that rule: the tools word it their own way
    (`agent.admin.check_party_limit`)."""
    if max_party_size < 1:
        return FormProblem("The largest group is at least 1.", Problem(code="group_too_small"))
    if max_party_size > MAX_SEATS:
        return FormProblem(
            f"The largest group is at most {MAX_SEATS}.",
            Problem(code="too_many_seats", seats=MAX_SEATS),
        )
    return None


def checked_rules(body: RulesInput) -> RulesInput:
    if problem := party_limit_problem(body.max_party_size):
        raise problem
    return body.model_copy(update={"cancellation_policy": body.cancellation_policy or None})


def checked_settings(body: SettingsInput) -> SettingsInput:
    if not body.name:
        raise FormProblem("The restaurant needs a name.", Problem(code="name_required"))
    return body.model_copy(
        update={"city": body.city or None, "tags": tuple(dict.fromkeys(body.tags))}
    )
