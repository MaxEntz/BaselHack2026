"""A BATP node: sessions, multiplexed requests and limits (PROTOCOL.md §3, §5, §6, §10).

The node knows nothing about restaurants: it authenticates callers and hands each request to
`handle`, which returns the intent's `result` or raises an `AgentError` (or `Refused`, to add
the merchant's fallback URL).
"""

import asyncio
import logging
import secrets
import ssl
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from pydantic import BaseModel, ValidationError

from batp.codec import MAX_FRAME, FrameError, FrameTooLarge, encode, read_frame
from batp.messages import (
    ADDRESSED,
    ALL_INTENTS,
    IN_FLIGHT_LIMIT,
    PROTOCOL_VERSION,
    AgentIdentity,
    ErrorBody,
    ErrorFrame,
    Hello,
    Intent,
    NodeIdentity,
    Reject,
    Request,
    Response,
    Welcome,
    compatible,
)
from batp.status import TOO_MANY_REQUESTS, Refused, error_body, status_for
from core.errors import AgentError

log = logging.getLogger("batp.node")

Handler = Callable[[AgentIdentity, Request], Awaitable[dict[str, Any]]]
# The addressed merchant's own booking page, for a 503 the node itself sends (§8); None if unknown.
FallbackUrl = Callable[[Request], Awaitable[str | None]]
Authenticate = Callable[[Hello], bool]
Send = Callable[[BaseModel], Awaitable[None]]

FAILED = "The merchant agent failed; try again later"
TOO_SLOW = "The merchant agent took too long; try again later"
TOO_LARGE = "The answer is too large to send"
# How long a 503 waits for the merchant's fallback URL before going without it.
FALLBACK_TIMEOUT = 1.0


class NodeServer:
    def __init__(
        self,
        *,
        node: NodeIdentity,
        handle: Handler,
        authenticate: Authenticate,
        intents: Sequence[Intent] = ALL_INTENTS,
        idle_timeout: float = 60.0,
        hello_timeout: float = 10.0,
        send_timeout: float = 10.0,
        handler_timeout: float = 10.0,
        max_in_flight: int = IN_FLIGHT_LIMIT,
        max_sessions: int = 64,
        max_malformed: int = 10,
        fallback_url: FallbackUrl | None = None,
    ) -> None:
        self.node = node
        self.handle = handle
        self.authenticate = authenticate
        self.intents = tuple(intents)
        self.idle_timeout = idle_timeout
        self.hello_timeout = hello_timeout
        self.send_timeout = send_timeout
        self.handler_timeout = handler_timeout
        self.max_in_flight = max_in_flight
        self.max_sessions = max_sessions
        self.max_malformed = max_malformed
        self.fallback_url = fallback_url
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    @property
    def port(self) -> int:
        assert self._server is not None, "start() first"
        port: int = self._server.sockets[0].getsockname()[1]
        return port

    async def start(self, host: str, port: int, *, tls: ssl.SSLContext | None = None) -> None:
        self._server = await asyncio.start_server(
            self._session, host, port, limit=MAX_FRAME, ssl=tls
        )
        log.info(
            "node listening",
            extra={"host": host, "port": self.port, "node": self.node.id, "tls": tls is not None},
        )

    async def serve(self, host: str, port: int, *, tls: ssl.SSLContext | None = None) -> None:
        await self.start(host, port, tls=tls)
        assert self._server is not None
        await self._server.serve_forever()

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        for writer in list(self._writers):
            writer.close()
        await self._server.wait_closed()
        self._server = None

    async def _session(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        lock = asyncio.Lock()
        peer = str(writer.get_extra_info("peername"))

        async def send(frame: BaseModel) -> None:
            # A peer that stops reading can't hold the session: past send_timeout, it ends.
            async with lock:
                writer.write(encode(frame))
                try:
                    await asyncio.wait_for(writer.drain(), self.send_timeout)
                except TimeoutError:
                    raise ConnectionError("peer stopped reading") from None

        tasks: set[asyncio.Task[None]] = set()
        try:
            if len(self._writers) >= self.max_sessions:
                log.warning("session refused: node busy", extra={"peer": peer})
                await send(Reject(status=503, message="Node busy, try again later"))
                return
            self._writers.add(writer)
            caller = await self._handshake(reader, send, peer)
            if caller is not None:
                await self._requests(caller, reader, send, tasks)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            for task in tasks:
                task.cancel()
            self._writers.discard(writer)
            writer.close()

    async def _handshake(
        self, reader: asyncio.StreamReader, send: Send, peer: str
    ) -> tuple[AgentIdentity, str] | None:
        """The caller's identity and session id, or None after a `reject`."""

        async def reject(status: int, message: str, agent: str | None = None) -> None:
            log.warning("session rejected", extra={"peer": peer, "agent": agent, "status": status})
            await send(Reject(status=status, message=message))

        try:
            frame = await asyncio.wait_for(read_frame(reader), self.hello_timeout)
        except TimeoutError:
            log.warning("session rejected: no hello", extra={"peer": peer})
            return None
        except FrameTooLarge:
            await reject(413, "Frame too large")
            return None
        except FrameError:
            await reject(400, "Malformed frame")
            return None
        if frame is None:
            return None
        if frame.get("type") != "hello":
            await reject(400, "Send hello first")
            return None
        try:
            hello = Hello.model_validate(frame)
        except ValidationError:
            await reject(400, "Invalid hello")
            return None
        if not compatible(hello.batp):
            await reject(426, f"This node speaks BATP {PROTOCOL_VERSION}", hello.agent.id)
            return None
        if not self.authenticate(hello):
            await reject(401, "Unknown agent or token", hello.agent.id)
            return None
        session = f"s-{secrets.token_hex(6)}"
        await send(Welcome(node=self.node, intents=self.intents, session=session))
        log.info(
            "session opened", extra={"agent": hello.agent.id, "session": session, "peer": peer}
        )
        return hello.agent, session

    async def _requests(
        self,
        caller: tuple[AgentIdentity, str],
        reader: asyncio.StreamReader,
        send: Send,
        tasks: set[asyncio.Task[None]],
    ) -> None:
        malformed = 0
        # The ids of the requests not answered yet: one of them can't be reused (§6).
        in_flight: set[str] = set()
        while True:
            if malformed >= self.max_malformed:
                log.warning(
                    "session closed: too many malformed frames", extra={"agent": caller[0].id}
                )
                return
            try:
                frame = await asyncio.wait_for(read_frame(reader), self.idle_timeout)
            except TimeoutError:
                if tasks:
                    continue  # not idle: answers are still on their way
                return
            except FrameTooLarge:
                await send(ErrorFrame(status=413, message="Frame too large"))
                return
            except FrameError:
                malformed += 1
                await send(ErrorFrame(status=400, message="Malformed frame: not JSON"))
                continue
            if frame is None:
                return
            raw_id = frame.get("id")
            known_id = raw_id if isinstance(raw_id, str) and 0 < len(raw_id) <= 64 else None
            if frame.get("type") != "request" or known_id is None:
                # No request id to answer: a session-level error frame (§6).
                malformed += 1
                await send(ErrorFrame(status=400, message="Not a request with a valid id"))
                continue
            if known_id in in_flight:
                # Not carried out: two answers with one id couldn't be told apart (§6).
                await send(ErrorFrame(status=400, message=f"Request {known_id} is in flight"))
                continue
            try:
                request = Request.model_validate(frame)
            except ValidationError:
                malformed += 1
                await send(_invalid(known_id, "Invalid request: check intent, merchant and params"))
                continue
            malformed = 0
            if request.intent not in self.intents:
                await send(_invalid(request.id, f"{request.intent} is not served by this node"))
                continue
            if (request.intent in ADDRESSED) != (request.merchant is not None):
                needs = "needs a merchant" if request.intent in ADDRESSED else "takes no merchant"
                await send(_invalid(request.id, f"{request.intent} {needs}"))
                continue
            if len(tasks) >= self.max_in_flight:
                # Not carried out; the caller may send it again after an earlier answer (§6).
                log.warning(
                    "request refused: too many in flight",
                    extra={"agent": caller[0].id, "request": request.id},
                )
                await send(_too_many(request.id))
                continue
            task = asyncio.create_task(self._answer(caller, request, send))
            tasks.add(task)
            in_flight.add(request.id)
            task.add_done_callback(tasks.discard)
            task.add_done_callback(_forget(in_flight, request.id))

    async def _answer(
        self, caller: tuple[AgentIdentity, str], request: Request, send: Send
    ) -> None:
        agent, session = caller
        started = time.monotonic()
        response = await self._respond(agent, request)
        try:
            await send(response)
        except FrameTooLarge:
            response = _unavailable(request.id, TOO_LARGE, await self._fallback(request))
            await send(response)
        except ConnectionError:
            return
        log.info(
            "batp request",
            extra={
                "agent": agent.id,
                "session": session,
                "request": request.id,
                "intent": request.intent,
                "merchant": request.merchant,
                "status": response.status,
                "latency_ms": round((time.monotonic() - started) * 1000),
            },
        )

    async def _respond(self, agent: AgentIdentity, request: Request) -> Response:
        try:
            async with asyncio.timeout(self.handler_timeout):
                result = await self.handle(agent, request)
            return Response(id=request.id, status=200, result=result)
        except Refused as refused:
            return Response(
                id=request.id,
                status=status_for(refused.error),
                error=error_body(refused.error, refused.fallback_url),
            )
        except AgentError as error:
            return Response(id=request.id, status=status_for(error), error=error_body(error))
        except TimeoutError:
            log.warning("request timed out", extra={"agent": agent.id, "intent": request.intent})
            return _unavailable(request.id, TOO_SLOW, await self._fallback(request))
        except Exception:
            log.exception("request failed", extra={"agent": agent.id, "intent": request.intent})
            return _unavailable(request.id, FAILED, await self._fallback(request))

    async def _fallback(self, request: Request) -> str | None:
        """The merchant's booking page for a 503 (§8): best effort, never a second failure."""
        if self.fallback_url is None or request.merchant is None:
            return None
        try:
            async with asyncio.timeout(FALLBACK_TIMEOUT):
                return await self.fallback_url(request)
        except Exception:
            log.warning("no fallback url", extra={"merchant": request.merchant}, exc_info=True)
            return None


def _forget(ids: set[str], request_id: str) -> Callable[[asyncio.Task[None]], None]:
    """A done callback that frees the request's id for reuse once it is answered."""
    return lambda _: ids.discard(request_id)


def _invalid(request_id: str | None, message: str) -> Response:
    return Response(
        id=request_id, status=400, error=ErrorBody(code="invalid_input", message=message)
    )


def _too_many(request_id: str) -> Response:
    error = ErrorBody(code="too_many_requests", message="Too many requests in flight")
    return Response(id=request_id, status=TOO_MANY_REQUESTS, error=error)


def _unavailable(request_id: str, message: str, fallback_url: str | None = None) -> Response:
    error = ErrorBody(code="agent_unavailable", message=message, fallback_url=fallback_url)
    return Response(id=request_id, status=503, error=error)
