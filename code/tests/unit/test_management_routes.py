"""The management site's routes (#129): frozen shapes, answering 501 until each issue lands."""

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from core.config import Settings
from core.models import ManagementError
from merchant_platform.api import create_app

SLUG = "la-graine-verte"
BOOKING = {
    "starts_at": "2026-10-31T20:00:00+01:00",
    "party_size": 2,
    "guest_name": "Kim Weber",
    "idempotency_key": "web-1",
}
MOVE = {"starts_at": "2026-10-31T20:30:00+01:00", "idempotency_key": "web-2"}
HOURS = {"services": [{"start": "11:30", "end": "14:30"}, {"start": "18:30", "end": "21:30"}]}
TABLES = {"tables": [{"label": "T1", "seats": 2}]}
RULES = {"max_party_size": 4, "fallback_url": "http://localhost:8081/#reserver"}
SETTINGS = {"name": "La Graine Verte", "city": "Mulhouse", "price_level": "€", "tags": []}

# (method, path under /merchants/{slug}, body, issue that implements it; None once built)
ROUTES: list[tuple[str, str, dict[str, Any] | None, str | None]] = [
    ("GET", "/dashboard?date=2026-10-31", None, "#135"),
    ("GET", "/dashboard", None, "#135"),  # the server's today
    ("GET", "/reservations?date=2026-10-31", None, "#134"),
    ("POST", "/reservations", BOOKING, "#134"),
    ("PATCH", "/reservations/R-001", MOVE, "#134"),
    ("POST", "/reservations/R-001/cancel", None, "#134"),
    ("GET", "/slots?date=2026-10-31&party_size=2&ignore=R-001", None, "#134"),
    ("GET", "/guests?q=kim", None, None),
    ("GET", "/search?q=kim", None, "#135"),
    ("GET", "/hours", None, "#133"),
    ("PUT", "/hours", HOURS, "#133"),
    ("GET", "/tables", None, "#133"),
    ("PUT", "/tables", TABLES, "#133"),
    ("GET", "/rules", None, "#133"),
    ("PUT", "/rules", RULES, "#133"),
    ("GET", "/settings", None, "#133"),
    ("PUT", "/settings", SETTINGS, "#133"),
    ("GET", "/blocked", None, "#133"),
    ("PUT", "/blocked?date=2026-11-01", {"times": ["13:00"]}, "#133"),
    ("POST", "/agent/pause", None, "#146"),
    ("POST", "/agent/resume", None, "#146"),
    ("GET", "/agent/requests?date=2026-10-31&refused=true", None, "#146"),
]
NOT_BUILT = [route for route in ROUTES if route[3]]


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"database_url": "postgresql+psycopg://u:p@localhost:5432/db"}
    return Settings(_env_file=None, **(values | overrides))  # type: ignore[call-arg]


def app(owner: str | None = SLUG) -> FastAPI:
    """Until #132, only the development merchant (PLATFORM_MERCHANT) has an owner."""
    return create_app(settings(platform_merchant=owner))


async def call(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    slug: str = SLUG,
    *,
    owner: str | None = SLUG,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app(owner))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        return await http.request(method, f"/merchants/{slug}{path}", json=body)


@pytest.mark.parametrize(("method", "path", "body", "issue"), NOT_BUILT)
async def test_each_route_answers_501_naming_its_issue(
    method: str, path: str, body: dict[str, Any] | None, issue: str
) -> None:
    response = await call(method, path, body)

    assert response.status_code == 501
    error = ManagementError.model_validate(response.json())
    assert error.error == "not_built_yet"
    assert issue in error.message


@pytest.mark.parametrize(("method", "path", "body", "issue"), [*ROUTES, ("GET", "", None, "")])
async def test_each_route_is_closed_to_anyone_but_the_owner(
    method: str, path: str, body: dict[str, Any] | None, issue: str | None
) -> None:
    anonymous = await call(method, path, body, owner=None)
    other = await call(method, path, body, owner="maison-albane")

    assert (anonymous.status_code, anonymous.json()["error"]) == (401, "not_signed_in")
    assert (other.status_code, other.json()["error"]) == (403, "other_merchant")


async def test_an_anonymous_request_is_refused_before_its_body_is_read() -> None:
    response = await call("PUT", "/hours", {"services": "nonsense"}, owner=None)

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("path", "body", "problem"),
    [
        ("/hours", {"services": []}, {"code": "no_services"}),
        (
            "/hours",
            {"services": [{"start": "18:30"}]},
            {"code": "service_incomplete", "services": [0]},
        ),
        (
            "/hours",
            {"services": [{"start": "11:30", "end": "14:30"}, {"start": "18:30", "end": ""}]},
            {"code": "service_incomplete", "services": [1]},
        ),
        (
            "/hours",
            {"services": [{"start": "11:30", "end": "14:30"}, {"start": "18:30", "end": "19:00"}]},
            {"code": "service_too_short", "services": [1]},
        ),
        (
            "/hours",
            {"services": [{"start": "18:30", "end": "21:30"}, {"start": "11:30", "end": "19:00"}]},
            {"code": "services_overlap", "services": [0, 1]},
        ),
        ("/tables", {"tables": []}, {"code": "no_tables"}),
        (
            "/tables",
            {"tables": [{"label": "T1", "seats": 2}, {"label": "T1", "seats": 4}]},
            {"code": "duplicate_table", "table": "T1"},
        ),
        (
            "/tables",
            {"tables": [{"label": "T1", "seats": 0}]},
            {"code": "table_without_seats", "table": "T1"},
        ),
        (
            "/tables",
            {"tables": [{"label": "T1", "seats": 1001}]},
            {"code": "too_many_seats", "table": "T1", "seats": 1000},
        ),
        ("/rules", RULES | {"max_party_size": 0}, {"code": "group_too_small"}),
        ("/settings", SETTINGS | {"name": "  "}, {"code": "name_required"}),
    ],
)
async def test_a_form_that_breaks_a_rule_names_it(
    path: str, body: dict[str, Any], problem: dict[str, Any]
) -> None:
    response = await call("PUT", path, body)

    assert response.status_code == 422
    error = ManagementError.model_validate(response.json())
    assert error.error == "invalid_input"
    assert error.problem is not None
    assert error.problem.model_dump(mode="json", exclude_defaults=True) == problem


async def test_a_built_route_without_an_agent_is_unavailable() -> None:
    response = await call("GET", "/guests")

    assert response.status_code == 503
    assert ManagementError.model_validate(response.json()).error == "agent_unavailable"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/dashboard?date=10-31", None),
        ("GET", "/reservations?date=1999-12-31", None),  # outside BookingDay
        ("GET", "/slots?date=2026-10-31&party_size=0", None),
        ("GET", "/slots?date=2026-10-31&party_size=2&ignore=R-1%00", None),
        ("GET", "/search?q=" + "x" * 201, None),
        ("GET", "/search?q=a%00b", None),
        ("GET", "/guests?q=a%00b", None),
        ("POST", "/reservations/R-1%00/cancel", None),
        ("POST", "/reservations", BOOKING | {"guest_name": " "}),
        ("POST", "/reservations", BOOKING | {"starts_at": "2026-10-31T20:00:00"}),  # no offset
        ("PATCH", "/reservations/R-001", {"starts_at": MOVE["starts_at"]}),  # no key
        ("PUT", "/hours", {"services": [{"start": "25:00", "end": "26:00"}]}),
        ("PUT", "/hours", {"services": [{"start": "11:00", "end": "12:00"}] * 9}),
        ("PUT", "/tables", {"tables": [{"label": "T" * 21, "seats": 2}]}),
        ("PUT", "/rules", RULES | {"cancellation_policy": "x" * 501}),
        ("PUT", "/rules", RULES | {"cancellation_policy": "line\nbreak"}),
        ("PUT", "/rules", RULES | {"slot_length_min": 90}),  # read-only: not an input
        ("PUT", "/settings", SETTINGS | {"tags": ["cheap"]}),
        ("PUT", "/settings", SETTINGS | {"website": "https://elsewhere.example"}),
        ("PUT", "/settings", SETTINGS | {"name": "N" * 81}),
        ("PUT", "/blocked?date=2026-11-01", {"times": ["25:00"]}),
        ("PUT", "/blocked?date=2026-11-01", {"times": ["12:00"] * 49}),
    ],
)
async def test_invalid_requests_are_invalid_input(
    method: str, path: str, body: dict[str, Any] | None
) -> None:
    response = await call(method, path, body)

    assert response.status_code == 422
    assert ManagementError.model_validate(response.json()).error == "invalid_input"


async def test_a_slug_outside_the_pattern_is_invalid_input() -> None:
    response = await call("GET", "/hours", slug="Not_A_Slug")

    assert response.status_code == 422


def test_the_openapi_schema_declares_each_answer() -> None:
    paths = app().openapi()["paths"]

    dashboard = paths["/merchants/{slug}/dashboard"]["get"]["responses"]["200"]
    assert dashboard["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Dashboard"
    }
    created = paths["/merchants/{slug}/reservations"]["post"]["responses"]
    assert "201" in created
    declared = {(path, method) for path, item in paths.items() for method in item}
    assert declared >= {(template(path), method.lower()) for method, path, _, _ in ROUTES}
    assert ("/merchants/{slug}", "get") in declared


def template(path: str) -> str:
    """The OpenAPI path of a route of ROUTES."""
    return "/merchants/{slug}" + path.split("?")[0].replace("R-001", "{reservation_id}")
