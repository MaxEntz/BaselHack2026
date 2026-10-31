"""MATP wire format: frames, status codes, versions (documentation/PROTOCOL.md §3, §6, §8, §13)."""

import asyncio
import json

import pytest
from pydantic import ValidationError

from core.errors import (
    AgentError,
    AgentUnavailable,
    InvalidInput,
    NotFound,
    PartyTooLarge,
    SlotUnavailable,
)
from matp.codec import MAX_FRAME, FrameError, FrameTooLarge, encode, read_frame
from matp.messages import ErrorBody, Request, Response, compatible
from matp.status import error_body, raise_for


def reader_with(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader(limit=MAX_FRAME)
    reader.feed_data(data)
    reader.feed_eof()
    return reader


async def test_a_frame_is_one_compact_json_line() -> None:
    frame = encode({"type": "request", "id": "r-1", "intent": "PING", "params": {}})

    assert frame.endswith(b"\n") and frame.count(b"\n") == 1
    assert await read_frame(reader_with(frame)) == json.loads(frame)


async def test_non_ascii_stays_readable_utf8() -> None:
    frame = encode(
        {"type": "request", "id": "r-1", "intent": "DISCOVER", "params": {"near": "Zürich"}}
    )

    assert "Zürich".encode() in frame


async def test_end_of_stream_is_none() -> None:
    assert await read_frame(reader_with(b"")) is None


@pytest.mark.parametrize("data", [b"not json\n", b"[1, 2]\n", b'"text"\n', b'{"type": "hello"'])
async def test_malformed_frames_are_frame_errors(data: bytes) -> None:
    with pytest.raises(FrameError):
        await read_frame(reader_with(data))


async def test_a_frame_above_64_kib_is_refused_before_parsing() -> None:
    oversized = b'{"x": "' + b"a" * MAX_FRAME + b'"}\n'

    with pytest.raises(FrameTooLarge):
        await read_frame(reader_with(oversized))


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (InvalidInput("bad"), 400),
        (NotFound("gone"), 404),
        (SlotUnavailable("taken"), 409),
        (PartyTooLarge("too many"), 413),
        (AgentUnavailable("down"), 503),
    ],
)
def test_business_errors_round_trip_through_status_codes(error: AgentError, status: int) -> None:
    body = error_body(error, fallback_url="http://zinc.example/book")
    response = Response(id="r-1", status=status, error=body)

    with pytest.raises(type(error), match=str(error)):
        raise_for(response)
    assert body.code == error.code
    expects_link = isinstance(error, PartyTooLarge | AgentUnavailable)
    assert (body.fallback_url is not None) == expects_link


def test_a_successful_response_raises_nothing() -> None:
    raise_for(Response(id="r-1", status=200, result={"pong": True}))


def test_an_error_status_without_a_business_code_is_agent_unavailable() -> None:
    with pytest.raises(AgentUnavailable, match="Unknown agent"):
        raise_for(Response(id=None, status=401, error=ErrorBody(message="Unknown agent")))


@pytest.mark.parametrize(
    ("version", "ok"), [("0.1", True), ("0.9", True), ("1.0", False), ("x", False), ("", False)]
)
def test_only_the_same_major_version_is_compatible(version: str, ok: bool) -> None:
    assert compatible(version) is ok


def test_unknown_intents_and_fields() -> None:
    with pytest.raises(ValidationError):
        Request.model_validate({"type": "request", "id": "r-1", "intent": "STEAL", "params": {}})
    # §13: unknown fields are ignored, so a newer caller can talk to this node.
    request = Request.model_validate(
        {"type": "request", "id": "r-1", "intent": "PING", "params": {}, "trace": "abc"}
    )
    assert request.intent == "PING"


@pytest.mark.parametrize(
    "data",
    [
        b'{"n": ' + b"9" * 5000 + b"}\n",  # integer too long to convert
        b"[" * 30000 + b"]" * 30000 + b"\n",  # nesting deeper than the parser allows
        b'\xef\xbb\xbf{"type": "hello"}\n',  # UTF-8 byte-order mark
        b'{"name": "\xff\xfe"}\n',  # invalid UTF-8
    ],
    ids=["huge-int", "deep-nesting", "bom", "bad-utf8"],
)
async def test_hostile_json_is_a_frame_error_not_a_crash(data: bytes) -> None:
    with pytest.raises(FrameError):
        await read_frame(reader_with(data[:MAX_FRAME]))


async def test_a_line_one_byte_over_the_limit_is_refused() -> None:
    line = b'{"x":"' + b"a" * (MAX_FRAME - 8) + b'"}\n'
    assert len(line) == MAX_FRAME + 1

    with pytest.raises(FrameTooLarge):
        await read_frame(reader_with(line))


def test_version_numbers_are_ascii_digits_only() -> None:
    assert compatible("0.²") is False


def test_the_token_never_shows_in_a_repr() -> None:
    from matp.messages import AgentIdentity, Hello

    agent = AgentIdentity(id="urn:matp:agent:x", name="X", kind="user-agent", operator="o")
    assert "s3cret-token" not in repr(Hello(matp="0.1", agent=agent, token="s3cret-token"))
