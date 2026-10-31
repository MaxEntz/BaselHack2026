"""BATP framing: one UTF-8 JSON object per line, at most 64 KiB (PROTOCOL.md §3).

Streams must be opened with `limit=MAX_FRAME`, so an oversized frame is refused before it is
buffered or parsed.
"""

import asyncio
import json
from typing import Any

from pydantic import BaseModel

MAX_FRAME = 64 * 1024


class FrameError(Exception):
    """The peer sent something that is not one JSON object on one line."""


class FrameTooLarge(FrameError):
    """A frame above MAX_FRAME: the session is closed with 413."""


def encode(frame: BaseModel | dict[str, Any]) -> bytes:
    """Compact JSON, UTF-8 kept readable, absent fields left out, then LF."""
    data = (
        frame.model_dump(mode="json", exclude_none=True) if isinstance(frame, BaseModel) else frame
    )
    line = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
    if len(line) > MAX_FRAME:
        raise FrameTooLarge(f"Frame of {len(line)} bytes, above {MAX_FRAME}")
    return line


async def read_frame(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    """The next frame, or None when the peer closed the stream between frames."""
    try:
        line = await reader.readuntil(b"\n")
    except asyncio.IncompleteReadError as error:
        if not error.partial.strip():
            return None
        raise FrameError("Truncated frame") from None
    except asyncio.LimitOverrunError:
        raise FrameTooLarge(f"Frame above {MAX_FRAME} bytes") from None
    if len(line) > MAX_FRAME:
        raise FrameTooLarge(f"Frame above {MAX_FRAME} bytes")
    try:
        # Strict UTF-8 first: a byte-order mark or invalid bytes are not a frame. Huge numbers
        # (ValueError) and deep nesting (RecursionError) are refused like any other bad JSON.
        data = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise FrameError("Not JSON") from None
    if not isinstance(data, dict):
        raise FrameError("Not a JSON object")
    return data
