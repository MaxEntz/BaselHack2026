"""A MATP client: one session, many requests in flight (PROTOCOL.md §5, §6).

The session opens on the first request and reopens on the next request after the node closed
it. When the session is lost under a request, a read-only intent is sent again once; a write
is never retried, since it may have gone through (BOOK and AMEND carry an idempotency key, so
the caller can retry them safely).
"""

import asyncio
import itertools
import ssl
from types import TracebackType
from typing import Any, Self

from pydantic import ValidationError

from core.errors import AgentUnavailable
from matp.codec import MAX_FRAME, FrameError, encode, read_frame
from matp.messages import (
    PROTOCOL_VERSION,
    AgentIdentity,
    Hello,
    Intent,
    Reject,
    Request,
    Response,
    Welcome,
)
from matp.status import raise_for

UNREACHABLE = "The MATP node is unreachable"
CLOSED = "The MATP session was closed before the answer"
READ_ONLY: frozenset[Intent] = frozenset({"PING", "DISCOVER", "AVAILABILITY", "RECALL"})


class SessionLost(AgentUnavailable):
    """The connection failed under a request, as opposed to an answer from the node."""


class MatpRejected(AgentUnavailable):
    """The node refused the session (`reject`): wrong token, unsupported version, …"""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class MatpClient:
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
    ) -> dict[str, Any]:
        """The intent's `result`; a business error is raised as its `AgentError`."""
        frame_params = params or {}
        try:
            response = await self._exchange(intent, merchant, frame_params)
        except SessionLost:
            if intent not in READ_ONLY:
                raise
            response = await self._exchange(intent, merchant, frame_params)
        raise_for(response)
        return response.result or {}

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None
        self._drop(self._writer)

    async def _exchange(
        self, intent: Intent, merchant: str | None, params: dict[str, Any]
    ) -> Response:
        async with self._connect_lock:
            if not self.connected:
                await self._connect()
            writer = self._writer
        if writer is None:
            raise SessionLost(CLOSED)
        request_id = f"r-{next(self._ids)}"
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
            hello = Hello(matp=PROTOCOL_VERSION, agent=self.agent, token=self.token)
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
                raise AgentUnavailable("The MATP node rejected the session") from None
            raise MatpRejected(reject.status, reject.message)
        try:
            self.welcome = Welcome.model_validate(frame)
        except ValidationError:
            writer.close()
            raise AgentUnavailable("The MATP node answered hello with something else") from None
        self._writer = writer
        self._reader_task = asyncio.create_task(self._read_responses(reader, writer))

    async def _read_responses(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while (frame := await read_frame(reader)) is not None:
                if frame.get("type") != "response":
                    continue
                response = Response.model_validate(frame)
                future = self._pending.get(response.id or "")
                if future is not None and not future.done():
                    future.set_result(response)
        except (ConnectionError, FrameError, ValidationError):
            pass
        finally:
            self._drop(writer)

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
