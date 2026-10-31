"""MATP frames (documentation/PROTOCOL.md §5 to §7).

Every frame is a JSON object with a `type`. Unknown fields are ignored (§13), so a newer peer
can talk to an older one within the same major version.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from core.models import Slug

PROTOCOL_VERSION = "0.1"
DEFAULT_PORT = 4710
# Every node accepts at least this many requests in flight per session (§6): a client that
# stays under it never gets a 429 from a node that follows the draft.
IN_FLIGHT_LIMIT = 32

Intent = Literal["DISCOVER", "AVAILABILITY", "BOOK", "AMEND", "CANCEL", "RECALL", "PING"]
ALL_INTENTS: tuple[Intent, ...] = (
    "DISCOVER",
    "AVAILABILITY",
    "BOOK",
    "AMEND",
    "CANCEL",
    "RECALL",
    "PING",
)
AgentKind = Literal["user-agent", "merchant-agent"]

RequestId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
Urn = Annotated[str, StringConstraints(pattern=r"^urn:matp:(agent|node):[a-z0-9][a-z0-9.-]{0,62}$")]
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
    matp: str
    agent: AgentIdentity
    token: str | None = Field(default=None, repr=False)


class Welcome(_Frame):
    type: Literal["welcome"] = "welcome"
    matp: str = PROTOCOL_VERSION
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


class Response(_Frame):
    type: Literal["response"] = "response"
    # None only when the request could not be read at all (malformed frame).
    id: RequestId | None
    status: int
    result: dict[str, Any] | None = None
    error: ErrorBody | None = None


def compatible(version: str) -> bool:
    """Same major version as this implementation (§13): `0.9` talks to `0.1`, `1.0` doesn't."""
    major, _, minor = version.partition(".")
    numbers = all(part.isascii() and part.isdigit() for part in (major, minor))
    return numbers and major == PROTOCOL_VERSION.partition(".")[0]
