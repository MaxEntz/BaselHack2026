"""BATP frames (documentation/PROTOCOL.md §5 to §7).

Every frame is a JSON object with a `type`. Unknown fields are ignored (§13), so a newer peer
can talk to an older one within the same major version.
"""

from collections.abc import Sequence
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from core.models import Slug

PROTOCOL_VERSION = "0.3"
DEFAULT_PORT = 4710
# Every node accepts at least this many requests in flight per session (§6): a client that
# stays under it never gets a 429 from a node that follows the draft.
IN_FLIGHT_LIMIT = 32

# 0.3 closed `AVAILABILITY`, `BOOK`, `AMEND` and `CANCEL` (#254): only the merchant's agent calls
# the booking tools, so a guest's agent books by talking to it. An old intent is an unknown one,
# `400 invalid_input`, in a session of any version.
Intent = Literal["DISCOVER", "TALK", "RECALL", "PING"]
ALL_INTENTS: tuple[Intent, ...] = ("DISCOVER", "TALK", "RECALL", "PING")
# The intents that address one merchant: `merchant` is required for them, absent otherwise (§6).
ADDRESSED: frozenset[Intent] = frozenset({"TALK"})
# The minor version that brought an intent (§14); the others are in every version. A session of
# an older minor is neither offered nor served it.
SINCE_MINOR: dict[Intent, int] = {"TALK": 2}
AgentKind = Literal["user-agent", "merchant-agent"]

MAX_REQUEST_ID = 64
RequestId = Annotated[str, StringConstraints(min_length=1, max_length=MAX_REQUEST_ID)]
Urn = Annotated[str, StringConstraints(pattern=r"^urn:batp:(agent|node):[a-z0-9][a-z0-9.-]{0,62}$")]
Label = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class _Frame(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class AgentIdentity(_Frame):
    """Who is calling: logged on every request of the session (§5)."""

    id: Urn
    name: Label
    kind: AgentKind
    operator: Label


class NodeIdentity(_Frame):
    id: Urn
    name: Label


class Hello(_Frame):
    type: Literal["hello"] = "hello"
    batp: str
    agent: AgentIdentity
    token: str | None = Field(default=None, repr=False)


class Welcome(_Frame):
    type: Literal["welcome"] = "welcome"
    batp: str = PROTOCOL_VERSION
    node: NodeIdentity
    intents: tuple[Intent, ...]
    session: str


class Reject(_Frame):
    type: Literal["reject"] = "reject"
    status: int
    message: str


class Request(_Frame):
    type: Literal["request"] = "request"
    id: RequestId
    intent: Intent
    merchant: Slug | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class ErrorBody(_Frame):
    """`code` is absent for protocol-level errors (401, 413, 426).

    The node sends a code of AGENTS.md §8.4 or `too_many_requests` (§8). Any string is read, so
    that a code this client doesn't know fails its own request, not the whole session.
    """

    code: str | None = None
    message: str
    fallback_url: str | None = None


class ErrorFrame(_Frame):
    """A problem no response can carry, because no request `id` can be attributed to it (§6):
    the frame isn't JSON, has no valid `type` or `id`, is too large, or reuses an `id` in
    flight. After a 413 the node closes the session; after a 400 it goes on."""

    type: Literal["error"] = "error"
    status: Literal[400, 413]
    message: str


class Response(_Frame):
    type: Literal["response"] = "response"
    # None only from a node older than #93: frames without a request id now get `ErrorFrame`.
    id: RequestId | None
    status: int
    result: dict[str, Any] | None = None
    error: ErrorBody | None = None


def compatible(version: str) -> bool:
    """Same major version as this implementation (§13): `0.9` talks to `0.1`, `1.0` doesn't."""
    major, _, minor = version.partition(".")
    numbers = all(part.isascii() and part.isdigit() for part in (major, minor))
    return numbers and major == PROTOCOL_VERSION.partition(".")[0]


def negotiate(version: str) -> str:
    """The session's version (§14): the highest minor both speak. `version` is `compatible`."""
    major, _, minor = PROTOCOL_VERSION.partition(".")
    return f"{major}.{min(int(minor), int(version.partition('.')[2]))}"


def intents_of(version: str, intents: Sequence[Intent]) -> tuple[Intent, ...]:
    """The `intents` that a session of `version` may use, in their order."""
    minor = int(version.partition(".")[2])
    return tuple(intent for intent in intents if SINCE_MINOR.get(intent, 0) <= minor)
