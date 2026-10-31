"""The platform's log setup (#84): every package's lines come out as JSON (§12)."""

import io
import json
import logging
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from merchant_platform.logs import configure_logging


@pytest.fixture
def written() -> Iterator[Callable[[], list[dict[str, Any]]]]:
    """Runs `configure_logging()` with its handler writing to a buffer; returns the lines
    written so far, parsed. The root logger is put back for the other tests."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    httpx_level = logging.getLogger("httpx").level
    configure_logging()
    (handler,) = root.handlers
    assert isinstance(handler, logging.StreamHandler)
    buffer = io.StringIO()
    handler.setStream(buffer)
    yield lambda: [json.loads(line) for line in buffer.getvalue().splitlines()]
    root.handlers[:] = handlers
    root.setLevel(level)
    logging.getLogger("httpx").setLevel(httpx_level)


def test_builder_and_agent_lines_come_out_as_json(
    written: Callable[[], list[dict[str, Any]]],
) -> None:
    logging.getLogger("builder.build").info("builder_cache", extra={"stage": "crawl", "hit": True})
    logging.getLogger("builder.llm").warning("llm_retry", extra={"error": "RateLimitError"})
    logging.getLogger("agent.service").info("slot blocked", extra={"merchant": "le-petit-zinc"})
    logging.getLogger("merchant_platform.api").info("chat_turn")

    logged = written()

    assert [(line["logger"], line["event"]) for line in logged] == [
        ("builder.build", "builder_cache"),
        ("builder.llm", "llm_retry"),
        ("agent.service", "slot blocked"),
        ("merchant_platform.api", "chat_turn"),
    ]
    assert (logged[0]["stage"], logged[0]["hit"]) == ("crawl", True)
    assert (logged[1]["level"], logged[1]["error"]) == ("warning", "RateLimitError")
    assert logged[2]["merchant"] == "le-petit-zinc"


def test_each_line_is_written_once_and_httpx_requests_are_quiet(
    written: Callable[[], list[dict[str, Any]]],
) -> None:
    logging.getLogger("merchant_platform.tools").info("admin_tool")
    logging.getLogger("httpx").info("HTTP Request: POST https://api.openai.com/v1/responses")

    assert [line["event"] for line in written()] == ["admin_tool"]
