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
HOURS = {"services": [{"start": "18:30", "end": "21:30"}]}
TABLES = {"tables": [{"label": "T1", "seats": 2}]}
RULES = {"max_party_size": 4, "fallback_url": "http://localhost:8081/#reserver"}
SETTINGS = {"name": "La Graine Verte", "city": "Mulhouse", "price_level": "€", "tags": []}

# (method, path under /merchants/{slug}, body, issue that implements it)
ROUTES: list[tuple[str, str, dict[str, Any] | None, str]] = [
    ("GET", "", None, "#132"),
    ("GET", "/dashboard?date=2026-10-31", None, "#135"),
    ("GET", "/reservations?date=2026-10-31", None, "#134"),
    ("POST", "/reservations", BOOKING, "#134"),
    ("PATCH", "/reservations/R-001", MOVE, "#134"),
    ("POST", "/reservations/R-001/cancel", None, "#134"),
    ("GET", "/slots?date=2026-10-31&party_size=2&ignore=R-001", None, "#134"),
    ("GET", "/guests?q=kim", None, "#144"),
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


def app() -> FastAPI:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, database_url="postgresql+psycopg://u:p@localhost:5432/db"
    )
    return create_app(settings)


async def call(
    method: str, path: str, body: dict[str, Any] | None = None, slug: str = SLUG
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        return await http.request(method, f"/merchants/{slug}{path}", json=body)


@pytest.mark.parametrize(("method", "path", "body", "issue"), ROUTES)
async def test_each_route_answers_501_naming_its_issue(
    method: str, path: str, body: dict[str, Any] | None, issue: str
) -> None:
    response = await call(method, path, body)

    assert response.status_code == 501
    error = ManagementError.model_validate(response.json())
    assert error.error == "agent_unavailable"
    assert issue in error.message


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/dashboard", None),  # date is required
        ("GET", "/reservations?date=1999-12-31", None),  # outside BookingDay
        ("GET", "/slots?date=2026-10-31&party_size=0", None),
        ("GET", "/search?q=" + "x" * 201, None),
        ("POST", "/reservations", BOOKING | {"guest_name": " "}),
        ("POST", "/reservations", BOOKING | {"starts_at": "2026-10-31T20:00:00"}),  # no offset
        ("PATCH", "/reservations/R-001", {"starts_at": MOVE["starts_at"]}),  # no key
        ("PUT", "/hours", {"services": []}),
        ("PUT", "/tables", {"tables": [{"label": "T1", "seats": 0}]}),
        ("PUT", "/rules", RULES | {"max_party_size": 0}),
        ("PUT", "/settings", SETTINGS | {"tags": ["cheap"]}),
        ("PUT", "/blocked?date=2026-11-01", {"times": ["25:00"]}),
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


def template(path: str) -> str:
    """The OpenAPI path of a route of ROUTES."""
    return "/merchants/{slug}" + path.split("?")[0].replace("R-001", "{reservation_id}")
