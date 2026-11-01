"""A BATP client: one session, many requests in flight (PROTOCOL.md §5, §6).

The session opens on the first request and reopens on the next request after the node closed
it. At most `IN_FLIGHT_LIMIT` requests it still waits on are in flight; the others wait for a
free slot. A request whose caller gave up may still run on the node, which then can answer a
429 to a later one: that request fails as `AgentUnavailable`, like any status it can't use.

When the session is lost under a request, an intent that is safe to resend is sent again once.
A write is never resent, since it may have gone through (BOOK and AMEND carry an idempotency
key, so the caller can retry them safely).
"""

import asyncio
import itertools
import logging
import ssl
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlsplit

from pydantic import ValidationError

from batp.codec import MAX_FRAME, FrameError, encode, read_frame
from batp.messages import (
    DEFAULT_PORT,
    IN_FLIGHT_LIMIT,
    PROTOCOL_VERSION,
    AgentIdentity,
    Hello,
    Intent,
    Reject,
    Request,
    Response,
    Welcome,
)
from batp.status import raise_for
from core.errors import AgentUnavailable

log = logging.getLogger("batp.client")

UNREACHABLE = "The BATP node is unreachable"
CLOSED = "The BATP session was closed before the answer"
UNREADABLE = "The BATP node sent an answer this client can't read"
# RECALL marks notices delivered, but returns them again for a day: resending it loses none (#91).
SAFE_TO_RESEND: frozenset[Intent] = frozenset({"PING", "DISCOVER", "RECALL"})


class SessionLost(AgentUnavailable):
    """The connection failed under a request, as opposed to an answer from the node."""


class BatpRejected(AgentUnavailable):
    """The node refused the session (`reject`): wrong token, unsupported version, …"""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def parse_batp_url(url: str) -> tuple[str, int]:
    """`batp://host[:port]` → host and port; a wrong value stops the caller at startup."""
    parts = urlsplit(url)
    if parts.scheme != "batp" or not parts.hostname:
        raise ValueError(f"BATP_URL must look like batp://host[:port], got {url!r}")
    return parts.hostname, parts.port or DEFAULT_PORT


class BatpClient:
    """One BATP session with a node, opened by the first request and by the next one after a loss.

    Requests are multiplexed on it (§6): each waits for the answer with its id. A node's refusal
    of the session is `BatpRejected`, a connection lost under a request `SessionLost`.
    """

    def __init__(
        self,
        host: str,
        port: int,
        agent: AgentIdentity,
        token: str | None = None,
        *,
        connect_timeout: float = 2.0,
        tls: ssl.SSLContext | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.agent = agent
        self.token = token
        self.connect_timeout = connect_timeout
        self.tls = tls
        self.welcome: Welcome | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[Response]] = {}
        self._ids = itertools.count(1)
        self._connect_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._in_flight = asyncio.Semaphore(IN_FLIGHT_LIMIT)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def request(
        self,
        intent: Intent,
        merchant: str | None = None,
        params: dict[str, Any] | None = None,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """The intent's `result`; a business error is raised as its `AgentError`.

        `request_id` names the frame, so that the caller's logs can be matched with the node's.
        It must be unique within the session; by default the client numbers its requests.
        """
        _, response = await self.exchange(intent, merchant, params, request_id=request_id)
        raise_for(response)
        return response.result or {}

    async def exchange(
        self,
        intent: Intent,
        merchant: str | None = None,
        params: dict[str, Any] | None = None,
        *,
        request_id: str | None = None,
    ) -> tuple[Request, Response]:
        """Both frames of a request: the node's business errors are answers here, not raised."""
        frame_params = params or {}
        frame_id = request_id or f"r-{next(self._ids)}"
        frame = Request(id=frame_id, intent=intent, merchant=merchant, params=frame_params)
        async with self._in_flight:
            try:
                return frame, await self._exchange(frame_id, intent, merchant, frame_params)
            except SessionLost:
                if intent not in SAFE_TO_RESEND:
                    raise
                return frame, await self._exchange(frame_id, intent, merchant, frame_params)

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None
        self._drop(self._writer)

    async def _exchange(
        self, request_id: str, intent: Intent, merchant: str | None, params: dict[str, Any]
    ) -> Response:
        async with self._connect_lock:
            if not self.connected:
                await self._connect()
            writer = self._writer
        if writer is None:
            raise SessionLost(CLOSED)
        if request_id in self._pending:
            raise ValueError(f"Request id already in flight: {request_id!r}")
        future: asyncio.Future[Response] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        frame = Request(id=request_id, intent=intent, merchant=merchant, params=params)
        try:
            try:
                async with self._write_lock:
                    writer.write(encode(frame))
                    await writer.drain()
            except (ConnectionError, OSError):
                self._drop(writer)
                raise SessionLost(UNREACHABLE) from None
            return await future
        finally:
            # A cancelled or timed-out request leaves nothing behind.
            self._pending.pop(request_id, None)

    async def _connect(self) -> None:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port, limit=MAX_FRAME, ssl=self.tls),
                self.connect_timeout,
            )
        except (OSError, TimeoutError):
            raise SessionLost(UNREACHABLE) from None
        try:
            hello = Hello(batp=PROTOCOL_VERSION, agent=self.agent, token=self.token)
            writer.write(encode(hello))
            await writer.drain()
            frame = await asyncio.wait_for(read_frame(reader), self.connect_timeout)
        except (OSError, TimeoutError, FrameError):
            writer.close()
            raise SessionLost(UNREACHABLE) from None
        if frame is not None and frame.get("type") == "reject":
            writer.close()
            try:
                reject = Reject.model_validate(frame)
            except ValidationError:
                raise AgentUnavailable("The BATP node rejected the session") from None
            raise BatpRejected(reject.status, reject.message)
        try:
            self.welcome = Welcome.model_validate(frame)
        except ValidationError:
            writer.close()
            raise AgentUnavailable("The BATP node answered hello with something else") from None
        self._writer = writer
        self._reader_task = asyncio.create_task(self._read_responses(reader, writer))

    async def _read_responses(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while (frame := await read_frame(reader)) is not None:
                if frame.get("type") == "error":
                    # Says which request of ours the node couldn't read; without this line, the
                    # only symptom would be that request timing out (#93).
                    log.warning(
                        "batp error frame",
                        extra={"status": frame.get("status"), "detail": frame.get("message")},
                    )
                if frame.get("type") != "response":
                    continue
                try:
                    response = Response.model_validate(frame)
                except ValidationError:
                    request_id = frame.get("id")
                    if not isinstance(request_id, str):
                        return  # no way to tell whose answer it is: the session is dropped
                    # Only its own request fails: the session and the others go on.
                    self._settle(request_id, AgentUnavailable(UNREADABLE))
                    continue
                self._settle(response.id, response)
        except ConnectionError:
            # The node restarted or closed the session: expected, the next request reconnects.
            log.debug("batp session lost", exc_info=True)
        except FrameError:
            log.warning("batp frame unreadable: session dropped", exc_info=True)
        finally:
            self._drop(writer)

    def _settle(self, request_id: str | None, outcome: Response | AgentUnavailable) -> None:
        future = self._pending.get(request_id) if request_id else None
        if future is None or future.done():
            return
        if isinstance(outcome, Response):
            future.set_result(outcome)
        else:
            future.set_exception(outcome)

    def _drop(self, writer: asyncio.StreamWriter | None) -> None:
        """Forget that connection; its requests still waiting get `SessionLost`.

        Only the current connection is dropped: a late end of an old connection must not
        fail the requests of the new one.
        """
        if writer is None or writer is not self._writer:
            if writer is not None:
                writer.close()
            return
        writer.close()
        self._writer = None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(SessionLost(CLOSED))
