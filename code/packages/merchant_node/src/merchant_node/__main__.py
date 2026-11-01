"""`uv run python -m merchant_node`: the BATP node on BATP_HOST:BATP_PORT (127.0.0.1:4710)."""

import asyncio
import logging
import secrets
import signal
import ssl
import sys
from collections.abc import Callable, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncEngine

from agent.service import PgAgentService
from batp.messages import Hello, NodeIdentity
from batp.server import NodeServer
from core.config import Settings, get_settings
from core.db import make_engine
from core.logs import use_json_logs
from merchant_node.agents import MerchantAgents
from merchant_node.requests_log import RequestLog, log_engine

log = logging.getLogger("merchant_node")

NODE = NodeIdentity(id="urn:batp:node:merchant-agents", name="Sésame")
# What systemd sends to stop the service, and Ctrl+C in a terminal.
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
MIN_TOKEN = 16


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


async def run(
    server: NodeServer,
    host: str,
    port: int,
    *,
    tls: ssl.SSLContext | None = None,
    engines: Sequence[AsyncEngine] = (),
) -> None:
    """Serve until SIGTERM or SIGINT, then stop: the sessions close, the recorders still
    running get up to `RECORD_TIMEOUT` to write their rows, and the engines are disposed.

    So a redeploy (`systemctl restart` sends SIGTERM) loses no request log row.
    """
    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()
    for signum in STOP_SIGNALS:
        loop.add_signal_handler(signum, stopping.set)
    try:
        await server.start(host, port, tls=tls)
        await stopping.wait()
        log.info("node stopping")
    finally:
        for signum in STOP_SIGNALS:
            loop.remove_signal_handler(signum)
        await server.stop()
        for engine in engines:
            await engine.dispose()


async def serve(settings: Settings) -> None:
    """The node these settings describe, until it is told to stop (`run`)."""
    tls = tls_context(settings)
    engine = make_engine(settings.database_url)
    records = log_engine(settings.database_url)
    service = PgAgentService(engine, tz=ZoneInfo(settings.merchant_tz))
    agents = MerchantAgents(engine, service)
    server = NodeServer(
        node=NODE,
        handle=agents,
        authenticate=token_check(settings),
        fallback_url=agents.fallback_url,
        record=RequestLog(records),
    )
    await run(server, settings.batp_host, settings.batp_port, tls=tls, engines=(engine, records))


def main() -> None:
    use_json_logs()
    settings = get_settings()
    if problems := exposure_problems(settings):
        sys.exit("; ".join(problems))
    asyncio.run(serve(settings))


if __name__ == "__main__":
    main()
