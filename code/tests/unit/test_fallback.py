"""Every failure of the agent becomes a `ToolError`, with the fallback URL when there is one."""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from pydantic import BaseModel, PositiveInt, ValidationError

from core.config import Settings
from core.errors import AgentUnavailable, InvalidInput, NotFound, PartyTooLarge, SlotUnavailable
from core.logs import JsonFormatter
from core.models import ErrorCode, ToolError
from gateway.discovery import RegistryEntry
from gateway.fallback import call_agent, call_merchant, call_service

URL = "https://le-petit-zinc.example/reservation"
ZINC = RegistryEntry("le-petit-zinc", "Le Petit Zinc", "live", URL)
GUEST = "Alex Martin"


def make_settings(**overrides: Any) -> Settings:
    values = {"database_url": "postgresql+psycopg://u:p@localhost:5432/db", "agent_timeout_ms": 50}
    return Settings(_env_file=None, **{**values, **overrides})


SETTINGS = make_settings()


def raising(error: Exception) -> Callable[[], Awaitable[str]]:
    async def op() -> str:
        raise error

    return op


async def ok() -> str:
    return "booked"


class _Party(BaseModel):
    size: PositiveInt


def validation_error() -> ValidationError:
    with pytest.raises(ValidationError) as caught:
        _Party(size=-1, guest=GUEST)
    return caught.value


async def test_result_is_returned_as_is() -> None:
    assert await call_agent(ZINC, "get_availability", ok, settings=SETTINGS) == "booked"


async def test_unknown_merchant_is_not_found_without_url() -> None:
    result = await call_agent(None, "get_availability", ok, settings=SETTINGS)

    assert isinstance(result, ToolError)
    assert result.error == "not_found"
    assert result.fallback_url is None


async def test_merchant_still_building_looks_unknown() -> None:
    building = RegistryEntry(ZINC.slug, ZINC.name, "building", URL)

    result = await call_agent(building, "get_availability", ok, settings=SETTINGS)

    assert isinstance(result, ToolError)
    assert result.error == "not_found"


async def test_down_merchant_gets_the_fallback_url_without_calling_the_agent() -> None:
    calls = 0

    async def op() -> str:
        nonlocal calls
        calls += 1
        return "booked"

    down = RegistryEntry(ZINC.slug, ZINC.name, "down", URL)
    result = await call_agent(down, "create_reservation", op, settings=SETTINGS)

    assert isinstance(result, ToolError)
    assert result.error == "agent_unavailable"
    assert str(result.fallback_url) == URL
    assert URL in result.message
    assert "get_my_reservations" not in result.message  # nothing was attempted
    assert calls == 0


@pytest.mark.parametrize(
    ("error", "code", "has_url"),
    [
        (AgentUnavailable("boom"), "agent_unavailable", True),
        (RuntimeError("boom"), "agent_unavailable", True),
        (TimeoutError(), "agent_unavailable", True),
        (PartyTooLarge("Le Petit Zinc takes up to 6"), "party_too_large", True),
        (SlotUnavailable("taken"), "slot_unavailable", False),
        (NotFound("no booking"), "not_found", False),
        (InvalidInput("bad"), "invalid_input", False),
    ],
)
async def test_errors_are_converted(error: Exception, code: ErrorCode, has_url: bool) -> None:
    result = await call_agent(ZINC, "get_availability", raising(error), settings=SETTINGS)

    assert isinstance(result, ToolError)
    assert result.error == code
    assert (result.fallback_url is not None) is has_url
    assert (URL in result.message) is has_url


async def test_party_too_large_keeps_the_merchants_message_and_adds_the_link() -> None:
    error = PartyTooLarge("Le Petit Zinc takes online bookings for up to 6 guests")

    result = await call_agent(ZINC, "get_availability", raising(error), settings=SETTINGS)

    assert isinstance(result, ToolError)
    assert result.message == (
        f"Le Petit Zinc takes online bookings for up to 6 guests. Book directly: {URL}"
    )


async def test_validation_error_names_the_field_without_the_rejected_value() -> None:
    result = await call_agent(
        ZINC, "get_availability", raising(validation_error()), settings=SETTINGS
    )

    assert isinstance(result, ToolError)
    assert result.error == "invalid_input"
    assert result.fallback_url is None
    assert "size" in result.message
    assert GUEST not in result.message


async def test_slow_agent_times_out_at_the_deadline() -> None:
    async def slow() -> str:
        await asyncio.sleep(0.5)
        return "booked"

    started = time.perf_counter()
    result = await call_agent(ZINC, "get_availability", slow, settings=SETTINGS)
    elapsed = time.perf_counter() - started

    assert isinstance(result, ToolError)
    assert result.error == "agent_unavailable"
    assert str(result.fallback_url) == URL
    assert elapsed < 0.3


@pytest.mark.parametrize(
    ("tool", "warned"),
    [
        ("create_reservation", True),
        ("update_reservation", True),
        ("cancel_reservation", True),
        ("get_availability", False),
    ],
)
async def test_a_write_that_timed_out_invites_to_check_before_retrying(
    tool: str, warned: bool
) -> None:
    result = await call_agent(ZINC, tool, raising(TimeoutError()), settings=SETTINGS)

    assert isinstance(result, ToolError)
    assert ("get_my_reservations" in result.message) is warned


async def test_unexpected_exception_is_logged_with_its_trace_but_never_returned(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "password=hunter2"

    with caplog.at_level(logging.ERROR, logger="gateway.tools"):
        result = await call_agent(
            ZINC, "get_availability", raising(RuntimeError(secret)), settings=SETTINGS
        )

    assert isinstance(result, ToolError)
    assert secret not in result.model_dump_json()
    assert "Traceback" not in result.model_dump_json()
    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1
    assert errors[0].exc_info is not None
    assert secret in JsonFormatter().format(errors[0])


async def test_cancellation_is_not_swallowed() -> None:
    async def slow() -> str:
        await asyncio.sleep(10)
        return "booked"

    task = asyncio.create_task(call_agent(ZINC, "get_availability", slow, settings=SETTINGS))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_call_is_logged_as_one_json_line_without_guest_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def booked_for_guest() -> str:
        return GUEST

    with caplog.at_level(logging.INFO, logger="gateway.tools"):
        await call_agent(ZINC, "create_reservation", booked_for_guest, settings=SETTINGS)

    records = [record for record in caplog.records if record.name == "gateway.tools"]
    assert len(records) == 1
    line = json.loads(JsonFormatter().format(records[0]))
    assert line["event"] == "tool_call"
    assert line["tool"] == "create_reservation"
    assert line["merchant"] == "le-petit-zinc"
    assert line["outcome"] == "ok"
    assert isinstance(line["latency_ms"], int)
    assert GUEST not in caplog.text


@pytest.mark.parametrize(
    ("error", "outcome"),
    [(PartyTooLarge("too many"), "party_too_large"), (RuntimeError("boom"), "agent_unavailable")],
)
async def test_log_outcome_is_the_error_code(
    error: Exception, outcome: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="gateway.tools"):
        await call_agent(ZINC, "get_availability", raising(error), settings=SETTINGS)

    logged = [record for record in caplog.records if record.getMessage() == "tool_call"]
    assert [record.__dict__["outcome"] for record in logged] == [outcome]


@pytest.mark.parametrize(
    ("targets", "times_out"),
    [("", True), ("le-petit-zinc", True), ("other, le-petit-zinc", True), ("other", False)],
)
async def test_chaos_delay_triggers_the_fallback_on_its_targets(
    targets: str, times_out: bool
) -> None:
    settings = make_settings(chaos_delay_ms=200, chaos_merchants=targets)

    result = await call_agent(ZINC, "get_availability", ok, settings=settings)

    assert isinstance(result, ToolError) is times_out


def test_chaos_settings_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAOS_DELAY_MS", "3000")
    monkeypatch.setenv("CHAOS_MERCHANTS", "le-petit-zinc,maison-albane")

    settings = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@localhost/db")

    assert settings.chaos_delay_ms == 3000
    assert settings.chaos_merchants == "le-petit-zinc,maison-albane"


async def test_call_service_returns_the_result_and_logs_no_merchant(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="gateway.tools"):
        assert await call_service("get_my_reservations", ok, settings=SETTINGS) == "booked"

    logged = [record for record in caplog.records if record.getMessage() == "tool_call"]
    assert [(r.__dict__["merchant"], r.__dict__["outcome"]) for r in logged] == [(None, "ok")]


async def test_call_service_logs_a_tool_error_returned_by_the_operation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    error = ToolError(error="agent_unavailable", message="down", fallback_url=URL)

    async def op() -> ToolError:
        return error

    with caplog.at_level(logging.INFO, logger="gateway.tools"):
        assert await call_service("search_merchants", op, settings=SETTINGS) == error

    logged = [record for record in caplog.records if record.getMessage() == "tool_call"]
    assert [r.__dict__["outcome"] for r in logged] == ["agent_unavailable"]


@pytest.mark.parametrize("error", [RuntimeError("secret"), TimeoutError(), InvalidInput("secret")])
async def test_call_service_failure_is_unavailable_without_url(error: Exception) -> None:
    result = await call_service("get_my_reservations", raising(error), settings=SETTINGS)

    assert isinstance(result, ToolError)
    assert result.error == "agent_unavailable"
    assert result.fallback_url is None
    assert "secret" not in result.message


async def test_call_merchant_finds_the_merchant_then_calls_the_agent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def find() -> RegistryEntry:
        return ZINC

    with caplog.at_level(logging.INFO, logger="gateway.tools"):
        result = await call_merchant(ZINC.slug, "get_availability", find, ok, settings=SETTINGS)

    assert result == "booked"
    logged = [record for record in caplog.records if record.getMessage() == "tool_call"]
    assert [r.__dict__["merchant"] for r in logged] == [ZINC.slug]


async def test_call_merchant_with_an_unknown_slug_is_not_found() -> None:
    async def find() -> None:
        return None

    result = await call_merchant("nobody", "get_availability", find, ok, settings=SETTINGS)

    assert isinstance(result, ToolError)
    assert result.error == "not_found"


@pytest.mark.parametrize("error", [RuntimeError("secret"), TimeoutError()])
async def test_call_merchant_without_registry_is_unavailable_without_url(
    error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    async def find() -> RegistryEntry:
        raise error

    with caplog.at_level(logging.INFO, logger="gateway.tools"):
        result = await call_merchant(ZINC.slug, "create_reservation", find, ok, settings=SETTINGS)

    assert isinstance(result, ToolError)
    assert result.error == "agent_unavailable"
    assert result.fallback_url is None
    assert "secret" not in result.message
    outcomes = [r.__dict__["outcome"] for r in caplog.records if r.getMessage() == "tool_call"]
    assert outcomes == ["agent_unavailable"]


async def test_call_service_gives_the_named_merchant_s_link_when_it_fails() -> None:
    async def named() -> RegistryEntry:
        return ZINC

    result = await call_service(
        "search_merchants", raising(AgentUnavailable("node")), settings=SETTINGS, named=named
    )

    assert isinstance(result, ToolError)
    assert result.error == "agent_unavailable"
    assert str(result.fallback_url) == URL


async def test_call_service_without_a_named_merchant_has_no_link() -> None:
    async def named() -> None:
        return None

    result = await call_service(
        "search_merchants", raising(AgentUnavailable("node")), settings=SETTINGS, named=named
    )

    assert isinstance(result, ToolError)
    assert result.fallback_url is None


async def test_a_registry_failure_does_not_hide_the_first_failure() -> None:
    async def named() -> RegistryEntry:
        raise RuntimeError("database down")

    result = await call_service(
        "search_merchants", raising(AgentUnavailable("node")), settings=SETTINGS, named=named
    )

    assert isinstance(result, ToolError)
    assert result.error == "agent_unavailable"
    assert result.fallback_url is None


async def test_the_named_merchant_is_only_looked_up_on_failure() -> None:
    calls = 0

    async def named() -> RegistryEntry:
        nonlocal calls
        calls += 1
        return ZINC

    assert await call_service("search_merchants", ok, settings=SETTINGS, named=named) == "booked"
    assert calls == 0


async def test_a_lost_node_is_not_logged_as_a_crash(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="gateway.tools"):
        await call_service(
            "get_my_reservations", raising(AgentUnavailable("node")), settings=SETTINGS
        )

    assert not caplog.records


async def test_the_log_line_has_no_matp_field_without_a_hop(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="gateway.tools"):
        await call_agent(ZINC, "get_availability", ok, settings=SETTINGS)

    line = json.loads(JsonFormatter().format(caplog.records[-1]))
    assert "matp_request" not in line


async def test_call_service_times_out() -> None:
    async def slow() -> str:
        await asyncio.sleep(0.5)
        return "done"

    started = time.perf_counter()
    result = await call_service("search_merchants", slow, settings=SETTINGS)

    assert isinstance(result, ToolError)
    assert time.perf_counter() - started < 0.3
