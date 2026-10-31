"""`uv run python -m merchant_node`: the BATP node on BATP_HOST:BATP_PORT (127.0.0.1:4710)."""

import asyncio
import logging
import secrets
import ssl
import sys
from collections.abc import Callable
from zoneinfo import ZoneInfo

from agent.service import PgAgentService
from batp.messages import Hello, NodeIdentity
from batp.server import NodeServer
from core.config import Settings, get_settings
from core.db import make_engine
from core.logs import json_handler
from merchant_node.agents import MerchantAgents

NODE = NodeIdentity(id="urn:batp:node:merchant-agents", name="Merchant Agents")
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


def main() -> None:
    root = logging.getLogger()
    root.handlers[:] = [json_handler()]
    root.setLevel(logging.INFO)
    settings = get_settings()
    if problems := exposure_problems(settings):
        sys.exit("; ".join(problems))
    engine = make_engine(settings.database_url)
    service = PgAgentService(engine, tz=ZoneInfo(settings.merchant_tz))
    agents = MerchantAgents(engine, service)
    server = NodeServer(
        node=NODE,
        handle=agents,
        authenticate=token_check(settings),
        fallback_url=agents.fallback_url,
    )
    asyncio.run(server.serve(settings.batp_host, settings.batp_port, tls=tls_context(settings)))


if __name__ == "__main__":
    main()
