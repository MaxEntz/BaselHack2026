"""The platform's log setup (#84): every package's lines come out as JSON (§12)."""

import io
import json
import logging
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from core.config import Settings
from core.models import MerchantProfile, TextEvent
from merchant_platform.api import create_app
from merchant_platform.logs import configure_logging, session_tag
from merchant_platform.sessions import Emit, Session


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


# A chat session's id claims its owner cookie after a build (#132): never in a log line (#220).
SESSION_ID = "o-c2Vzc2lvbi1pZC1pbi1hLWxvZw"
SLUG = "la-graine-verte"


async def test_a_chat_turn_names_its_session_by_a_tag_never_by_its_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def assistant(session: Session, message: str, emit: Emit) -> None:
        if message == "fail":
            # As a `KeyError` on the id would: the exception itself holds it.
            raise RuntimeError(f"no history for session {session.id}")
        await emit(TextEvent(delta="Noted."))

    async def profiles(slug: str) -> MerchantProfile | None:
        return MerchantProfile.model_construct(slug=SLUG, name="La Graine Verte")

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url="postgresql+psycopg://u:p@localhost:5432/db",
        platform_merchant=SLUG,
    )
    app = create_app(settings, assistant=assistant, profiles=profiles)
    caplog.set_level(logging.DEBUG)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        for message in ("Hello", "fail"):
            reply = await http.post("/chat", json={"session_id": SESSION_ID, "message": message})
            assert reply.status_code == 200

    # The platform's own lines; httpx's are the test client's, which sent the id.
    platform = [record for record in caplog.records if not record.name.startswith("httpx")]
    turns = [r for r in platform if r.getMessage() in ("chat_turn", "chat_turn_failed")]
    assert [r.getMessage() for r in turns] == ["chat_turn", "chat_turn_failed", "chat_turn"]
    assert {r.__dict__["session"] for r in turns} == {session_tag(SESSION_ID)}
    # The traceback is still logged, where `log.exception` puts it, with the tag for the id.
    failed = turns[1].__dict__["exc"]
    assert "RuntimeError: no history for session " + session_tag(SESSION_ID) in failed
    assert all(SESSION_ID not in str(record.__dict__) for record in platform)


def test_a_session_tag_is_short_stable_and_distinct() -> None:
    tag = session_tag(SESSION_ID)

    assert tag == session_tag(SESSION_ID)
    assert len(tag) == 8 and tag not in SESSION_ID
    assert tag != session_tag(SESSION_ID + "x")
