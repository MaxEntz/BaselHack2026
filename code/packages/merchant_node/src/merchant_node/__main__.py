"""`uv run python -m merchant_node`: the BATP node on BATP_HOST:BATP_PORT (127.0.0.1:4710)."""

import asyncio
import logging
import secrets
import signal
import ssl
import sys
from collections.abc import Callable, Sequence
from contextlib import AsyncExitStack
from zoneinfo import ZoneInfo

from openai import Timeout
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.service import AgentService, PgAgentService
from agent.talk import MerchantAgent, registry_merchants
from batp.messages import Hello, NodeIdentity
from batp.server import INTENT_TIMEOUTS, STOP_GRACE, NodeServer
from core.config import Settings, get_settings
from core.db import make_engine
from core.llm import OpenAIClient, openai_connection
from core.logs import use_json_logs
from merchant_node.agents import MerchantAgents
from merchant_node.requests_log import RequestLog, log_engine

log = logging.getLogger("merchant_node")

NODE = NodeIdentity(id="urn:batp:node:merchant-agents", name="Sésame")
# What systemd sends to stop the service, and Ctrl+C in a terminal.
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
MIN_TOKEN = 16
# A model call that stops sending gives up after 20 s, like the agent's whole turn
# (`agent.talk.TURN_SECONDS`): both stay under the node's 25 s for `TALK` (`TALK_TIMEOUT`).
LLM_TIMEOUT = Timeout(20, connect=5)


def exposure_problems(settings: Settings) -> list[str]:
    """Why the node must not start with these settings (PROTOCOL.md §3, §10); empty if fine.

    Beyond loopback, the node needs tokens (who may call) and TLS (tokens and guest names
    never travel in clear). Tokens are long enough to resist guessing.
    """
    problems = []
    tokens = [token.get_secret_value() for token in settings.batp_tokens.values()]
    if any(len(token) < MIN_TOKEN for token in tokens):
        problems.append(f"BATP_TOKENS: every token needs at least {MIN_TOKEN} characters")
    if settings.batp_host not in LOOPBACK:
        if not tokens:
            problems.append("BATP_TOKENS is required when the node listens beyond loopback")
        if not (settings.batp_tls_cert and settings.batp_tls_key):
            problems.append(
                "BATP_TLS_CERT and BATP_TLS_KEY are required when the node listens beyond loopback"
            )
    return problems


def tls_context(settings: Settings) -> ssl.SSLContext | None:
    if not (settings.batp_tls_cert and settings.batp_tls_key):
        return None
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(settings.batp_tls_cert, settings.batp_tls_key)
    return context


def token_check(settings: Settings) -> Callable[[Hello], bool]:
    """With BATP_TOKENS, only listed agents with their token get in. Without, any agent does:
    allowed on loopback only, for development."""
    tokens = {agent: token.get_secret_value() for agent, token in settings.batp_tokens.items()}

    def authenticate(hello: Hello) -> bool:
        if not tokens:
            return True
        expected = tokens.get(hello.agent.id)
        given = hello.token or ""
        return expected is not None and secrets.compare_digest(given.encode(), expected.encode())

    return authenticate


def merchant_agent(
    settings: Settings, engine: AsyncEngine, service: AgentService
) -> MerchantAgent | None:
    """The merchants' AI agent on OPENAI_API_KEY and OPENAI_MODEL, the builder's (#250); None
    without a key: `TALK` then answers 503 with the fallback URL, the other intents work."""
    connection = openai_connection(settings, LLM_TIMEOUT)
    if connection is None:
        log.warning("no OPENAI_API_KEY: TALK answers with the merchants' fallback URLs")
        return None
    llm = OpenAIClient(
        connection, settings.openai_model, reasoning_effort=settings.agent_reasoning_effort
    )
    return MerchantAgent(llm, service, registry_merchants(engine), ZoneInfo(settings.merchant_tz))


async def run(
    server: NodeServer,
    host: str,
    port: int,
    *,
    tls: ssl.SSLContext | None = None,
    engines: Sequence[AsyncEngine] = (),
) -> None:
    """Serve until SIGTERM or SIGINT, then drain (`NodeServer.stop`): no new session, the
    answers in flight finish within the grace, the sessions close, the recorders get up to
    `RECORD_TIMEOUT` to write their rows; then the engines are disposed.

    So a redeploy (`systemctl restart` sends SIGTERM) loses neither an answer on its way nor
    its request log row. The handlers stay until the end: a second signal while draining
    changes nothing, where the default one would kill the process before the engines close.
    The exit stack unwinds in reverse, each step even when the one before failed.
    """
    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()
    async with AsyncExitStack() as cleanup:
        for signum in STOP_SIGNALS:
            loop.add_signal_handler(signum, stopping.set)
            cleanup.callback(loop.remove_signal_handler, signum)
        for engine in engines:
            cleanup.push_async_callback(engine.dispose)
        cleanup.push_async_callback(server.stop)
        await server.start(host, port, tls=tls)
        await stopping.wait()
        log.info("node stopping")


async def serve(settings: Settings) -> None:
    """The node these settings describe, until it is told to stop (`run`)."""
    tls = tls_context(settings)
    engine = make_engine(settings.database_url)
    records = log_engine(settings.database_url)
    service = PgAgentService(engine, tz=ZoneInfo(settings.merchant_tz))
    agents = MerchantAgents(engine, service, merchant_agent(settings, engine, service))
    server = node_server(settings, agents, RequestLog(records))
    await run(server, settings.batp_host, settings.batp_port, tls=tls, engines=(engine, records))


def node_server(settings: Settings, agents: MerchantAgents, record: RequestLog) -> NodeServer:
    """The BATP server around the merchants' agents, with the deadlines of `batp.server`."""
    return NodeServer(
        node=NODE,
        handle=agents,
        authenticate=token_check(settings),
        fallback_url=agents.fallback_url,
        record=record,
        intent_timeouts=INTENT_TIMEOUTS,
        # Never shorter than an answer in flight may take: the gateway's whole tool call, and
        # each intent's own deadline, `TALK`'s among them, whose answer may report a write
        # already made.
        stop_grace=max(STOP_GRACE, settings.agent_timeout_ms / 1000, *INTENT_TIMEOUTS.values()),
    )


def main() -> None:
    use_json_logs()
    settings = get_settings()
    if problems := exposure_problems(settings):
        sys.exit("; ".join(problems))
    asyncio.run(serve(settings))


if __name__ == "__main__":
    main()
