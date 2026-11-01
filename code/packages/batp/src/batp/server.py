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
from dataclasses import dataclass
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel, ValidationError

from batp.codec import MAX_FRAME, FrameError, FrameTooLarge, encode, read_frame
from batp.messages import (
    ADDRESSED,
    ALL_INTENTS,
    IN_FLIGHT_LIMIT,
    MAX_REQUEST_ID,
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
    intents_of,
    negotiate,
)
from batp.status import (
    BAD_REQUEST,
    CONTENT_TOO_LARGE,
    OK,
    SERVICE_UNAVAILABLE,
    TOO_MANY_REQUESTS,
    UNAUTHORIZED,
    UPGRADE_REQUIRED,
    Refused,
    error_body,
    status_for,
)
from core.errors import AgentError

log = logging.getLogger("batp.node")

Handler = Callable[[AgentIdentity, Request], Awaitable[dict[str, Any]]]
# The addressed merchant's own booking page, for a 503 the node itself sends (§8); None if unknown.
FallbackUrl = Callable[[Request], Awaitable[str | None]]
Authenticate = Callable[[Hello], bool]
# Told about each answered request, after the answer went out: a request log, for example.
# Never awaited by the answer, so it can neither delay it nor fail it.
Recorder = Callable[[AgentIdentity, Request, Response], Awaitable[None]]
Send = Callable[[BaseModel], Awaitable[None]]


class Caller(NamedTuple):
    """A session's caller, once `welcome` went out."""

    agent: AgentIdentity
    session: str
    # The intents offered in `welcome`: this node's, within the session's version (§14).
    intents: tuple[Intent, ...]


FAILED = "The merchant agent failed; try again later"
TOO_SLOW = "The merchant agent took too long; try again later"
TOO_LARGE = "The answer is too large to send"
# How long a 503 waits for the merchant's fallback URL before going without it.
FALLBACK_TIMEOUT = 1.0
# How long a recorder may take, and how long `stop()` waits for the ones still running.
RECORD_TIMEOUT = 5.0
# How long `stop()` lets the answers in flight finish before it closes the sessions: longer than
# the gateway's whole tool call (AGENT_TIMEOUT_MS, 2 s by default), which no longer waits after.
STOP_GRACE = 3.0

# Default limits of a node (PROTOCOL.md §10), in seconds where they are times.
IDLE_TIMEOUT = 60.0  # no frame and no request in flight: the session ends
HELLO_TIMEOUT = 10.0  # to say `hello` after connecting
SEND_TIMEOUT = 10.0  # for a peer to read a frame: one that stops reading loses its session
HANDLER_TIMEOUT = 10.0  # per request: past it, a 503
MAX_SESSIONS = 64  # open at once: one more gets a `reject` 503
MAX_MALFORMED = 10  # malformed frames in a row before the session is closed


class NodeServer:
    """A BATP node listening for agents: it opens their sessions, screens each request frame and
    hands each request to `handle`, many in flight per session, within the limits of §10.

    `stop()` drains it: no new session, the answers in flight get up to `stop_grace` to go
    out, then the sessions close and the recorders still running get up to `RECORD_TIMEOUT`.
    """

    def __init__(
        self,
        *,
        node: NodeIdentity,
        handle: Handler,
        authenticate: Authenticate,
        intents: Sequence[Intent] = ALL_INTENTS,
        idle_timeout: float = IDLE_TIMEOUT,
        hello_timeout: float = HELLO_TIMEOUT,
        send_timeout: float = SEND_TIMEOUT,
        handler_timeout: float = HANDLER_TIMEOUT,
        max_in_flight: int = IN_FLIGHT_LIMIT,
        max_sessions: int = MAX_SESSIONS,
        max_malformed: int = MAX_MALFORMED,
        fallback_url: FallbackUrl | None = None,
        record: Recorder | None = None,
        stop_grace: float = STOP_GRACE,
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
        self.record = record
        self.stop_grace = stop_grace
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        # Every session's answers in flight, which `stop()` lets finish.
        self._answers: set[asyncio.Task[None]] = set()
        # Running recorders: kept so they aren't garbage-collected, awaited a little by stop().
        self._records: set[asyncio.Task[None]] = set()

    @property
    def port(self) -> int:
        port: int = self._listening().sockets[0].getsockname()[1]
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
        await self._listening().serve_forever()

    def _listening(self) -> asyncio.Server:
        if self._server is None:
            raise RuntimeError("The node is not listening: call start() first")
        return self._server

    async def stop(self) -> None:
        """Stop accepting sessions, let the answers in flight finish within `stop_grace`, close
        the sessions (cancelling what is still running), then wait for the recorders."""
        if self._server is None:
            return
        self._server.close()
        await self._finish_answers()
        for writer in list(self._writers):
            writer.close()
        await self._server.wait_closed()
        self._server = None
        if self._records:
            await asyncio.wait(self._records, timeout=RECORD_TIMEOUT)

    async def _finish_answers(self) -> None:
        """Wait for the answers in flight, those an open session sends meanwhile too, until
        none is left or `stop_grace` has passed."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.stop_grace
        while self._answers and (left := deadline - loop.time()) > 0:
            await asyncio.wait(set(self._answers), timeout=left)
        if self._answers:
            log.warning("node stopped with answers in flight", extra={"count": len(self._answers)})

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
                await send(Reject(status=SERVICE_UNAVAILABLE, message="Node busy, try again later"))
                return
            self._writers.add(writer)
            caller = await self._handshake(reader, send, peer)
            if caller is not None:
                await self._requests(caller, reader, send, tasks)
        except (ConnectionError, asyncio.IncompleteReadError):
            # A peer that leaves or stops reading: expected, so only at debug level.
            log.debug("session ended: connection lost", extra={"peer": peer}, exc_info=True)
        finally:
            for task in tasks:
                task.cancel()
            self._writers.discard(writer)
            writer.close()

    async def _handshake(
        self, reader: asyncio.StreamReader, send: Send, peer: str
    ) -> Caller | None:
        """The caller, or None after a `reject`."""

        async def reject(status: int, message: str, agent: str | None = None) -> None:
            log.warning("session rejected", extra={"peer": peer, "agent": agent, "status": status})
            await send(Reject(status=status, message=message))

        try:
            frame = await asyncio.wait_for(read_frame(reader), self.hello_timeout)
        except TimeoutError:
            log.warning("session rejected: no hello", extra={"peer": peer})
            return None
        except FrameTooLarge:
            await reject(CONTENT_TOO_LARGE, "Frame too large")
            return None
        except FrameError:
            await reject(BAD_REQUEST, "Malformed frame")
            return None
        if frame is None:
            return None
        if frame.get("type") != "hello":
            await reject(BAD_REQUEST, "Send hello first")
            return None
        try:
            hello = Hello.model_validate(frame)
        except ValidationError:
            await reject(BAD_REQUEST, "Invalid hello")
            return None
        if not compatible(hello.batp):
            await reject(
                UPGRADE_REQUIRED, f"This node speaks BATP {PROTOCOL_VERSION}", hello.agent.id
            )
            return None
        if not self.authenticate(hello):
            await reject(UNAUTHORIZED, "Unknown agent or token", hello.agent.id)
            return None
        session = f"s-{secrets.token_hex(6)}"
        version = negotiate(hello.batp)
        intents = intents_of(version, self.intents)
        await send(Welcome(batp=version, node=self.node, intents=intents, session=session))
        log.info(
            "session opened",
            extra={"agent": hello.agent.id, "session": session, "peer": peer, "batp": version},
        )
        return Caller(hello.agent, session, intents)

    async def _requests(
        self,
        caller: Caller,
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
                    "session closed: too many malformed frames", extra={"agent": caller.agent.id}
                )
                return
            try:
                frame = await asyncio.wait_for(read_frame(reader), self.idle_timeout)
            except TimeoutError:
                if tasks:
                    continue  # not idle: answers are still on their way
                return
            except FrameTooLarge:
                await send(ErrorFrame(status=CONTENT_TOO_LARGE, message="Frame too large"))
                return
            except FrameError:
                malformed += 1
                await send(ErrorFrame(status=BAD_REQUEST, message="Malformed frame: not JSON"))
                continue
            if frame is None:
                return
            screened = self._screen(frame, in_flight, caller.intents)
            if isinstance(screened, _Refusal):
                malformed = _counted(malformed, screened.effect)
                await send(screened.answer)
                continue
            malformed = 0
            if len(tasks) >= self.max_in_flight:
                # Not carried out; the caller may send it again after an earlier answer (§6).
                log.warning(
                    "request refused: too many in flight",
                    extra={"agent": caller.agent.id, "request": screened.id},
                )
                await send(_too_many(screened.id))
                continue
            task = asyncio.create_task(self._answer(caller, screened, send))
            tasks.add(task)
            self._answers.add(task)
            in_flight.add(screened.id)
            task.add_done_callback(tasks.discard)
            task.add_done_callback(self._answers.discard)
            task.add_done_callback(_forget(in_flight, screened.id))

    def _screen(
        self, frame: dict[str, Any], in_flight: set[str], intents: tuple[Intent, ...]
    ) -> "Request | _Refusal":
        """The request `frame` holds, if this node carries it out; else what to answer.

        `intents` are the session's: this node's, within the session's version (§14).
        """
        raw_id = frame.get("id")
        known_id = raw_id if isinstance(raw_id, str) and 0 < len(raw_id) <= MAX_REQUEST_ID else None
        if frame.get("type") != "request" or known_id is None:
            # No request id to answer: a session-level error frame (§6).
            message = "Not a request with a valid id"
            return _Refusal(ErrorFrame(status=BAD_REQUEST, message=message), "malformed")
        if known_id in in_flight:
            # Not carried out: two answers with one id couldn't be told apart (§6).
            message = f"Request {known_id} is in flight"
            return _Refusal(ErrorFrame(status=BAD_REQUEST, message=message), "duplicate")
        try:
            request = Request.model_validate(frame)
        except ValidationError:
            message = "Invalid request: check intent, merchant and params"
            return _Refusal(_invalid(known_id, message), "malformed")
        if request.intent not in intents:
            message = f"{request.intent} is not served by this node"
            return _Refusal(_invalid(request.id, message), "readable")
        if (request.intent in ADDRESSED) != (request.merchant is not None):
            needs = "needs a merchant" if request.intent in ADDRESSED else "takes no merchant"
            return _Refusal(_invalid(request.id, f"{request.intent} {needs}"), "readable")
        return request

    async def _answer(self, caller: Caller, request: Request, send: Send) -> None:
        agent, session = caller.agent, caller.session
        started = time.monotonic()
        response = await self._respond(agent, request)
        try:
            try:
                delivered = await _deliver(send, response)
            except FrameTooLarge:
                response = _unavailable(request.id, TOO_LARGE, await self._fallback(request))
                delivered = await _deliver(send, response)
        finally:
            # Carried out whether or not the answer got through (a booking may exist): recorded
            # once, even when the session's end cancels this task mid-send.
            self._record(agent, request, response)
        line = {
            "agent": agent.id,
            "session": session,
            "request": request.id,
            "intent": request.intent,
            "merchant": request.merchant,
            "status": response.status,
            "latency_ms": round((time.monotonic() - started) * 1000),
        }
        if delivered:
            log.info("batp request", extra=line)
        else:
            log.warning("batp answer lost: the session ended first", extra=line)

    async def _respond(self, agent: AgentIdentity, request: Request) -> Response:
        try:
            async with asyncio.timeout(self.handler_timeout):
                result = await self.handle(agent, request)
            return Response(id=request.id, status=OK, result=result)
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

    def _record(self, agent: AgentIdentity, request: Request, response: Response) -> None:
        """Hand the answered request to the recorder, in its own task."""
        if self.record is None:
            return
        task = asyncio.create_task(self._run_recorder(self.record, agent, request, response))
        self._records.add(task)
        task.add_done_callback(self._records.discard)

    async def _run_recorder(
        self, record: Recorder, agent: AgentIdentity, request: Request, response: Response
    ) -> None:
        try:
            async with asyncio.timeout(RECORD_TIMEOUT):
                await record(agent, request, response)
        except Exception:
            log.warning(
                "request not recorded",
                extra={"agent": agent.id, "request": request.id, "intent": request.intent},
                exc_info=True,
            )

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


async def _deliver(send: Send, frame: BaseModel) -> bool:
    """Send `frame`; False when the session is gone. `FrameTooLarge` goes to the caller."""
    try:
        await send(frame)
    except ConnectionError:
        return False
    return True


# What a refused frame does to the count of malformed frames in a row (§6): one more
# (`malformed`), back to none since it was a readable request (`readable`), or no change (a
# `duplicate` id, still in flight).
Effect = Literal["malformed", "readable", "duplicate"]


@dataclass(frozen=True)
class _Refusal:
    """A frame the node answers without carrying it out."""

    answer: ErrorFrame | Response
    effect: Effect


def _counted(malformed: int, effect: Effect) -> int:
    """The count of malformed frames in a row after a refusal with this `effect`."""
    if effect == "malformed":
        return malformed + 1
    return 0 if effect == "readable" else malformed


def _forget(ids: set[str], request_id: str) -> Callable[[asyncio.Task[None]], None]:
    """A done callback that frees the request's id for reuse once it is answered."""
    return lambda _: ids.discard(request_id)


def _invalid(request_id: str, message: str) -> Response:
    return Response(
        id=request_id, status=BAD_REQUEST, error=ErrorBody(code="invalid_input", message=message)
    )


def _too_many(request_id: str) -> Response:
    error = ErrorBody(code="too_many_requests", message="Too many requests in flight")
    return Response(id=request_id, status=TOO_MANY_REQUESTS, error=error)


def _unavailable(request_id: str, message: str, fallback_url: str | None = None) -> Response:
    error = ErrorBody(code="agent_unavailable", message=message, fallback_url=fallback_url)
    return Response(id=request_id, status=SERVICE_UNAVAILABLE, error=error)
