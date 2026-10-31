"""Who gets into the node (PROTOCOL.md §5, §10): tokens, and loopback-only without them."""

from typing import Any

import pytest

from core.config import Settings
from matp.messages import AgentIdentity, Hello
from merchant_node import __main__ as node_main

GATEWAY = AgentIdentity(
    id="urn:matp:agent:claude-gateway", name="Gateway", kind="user-agent", operator="team"
)
STRANGER = AgentIdentity(
    id="urn:matp:agent:stranger", name="Stranger", kind="user-agent", operator="nobody"
)


def settings(**values: Any) -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://u:p@localhost/db", **values)


@pytest.mark.parametrize(
    ("agent", "token", "allowed"),
    [
        (GATEWAY, "gw-token-0123456789", True),
        (GATEWAY, "wrong", False),
        (GATEWAY, None, False),
        (STRANGER, "gw-token-0123456789", False),
    ],
)
def test_with_tokens_only_listed_agents_with_their_token_get_in(
    agent: AgentIdentity, token: str | None, allowed: bool
) -> None:
    check = node_main.token_check(settings(matp_tokens={GATEWAY.id: "gw-token-0123456789"}))

    assert check(Hello(matp="0.1", agent=agent, token=token)) is allowed


def test_without_tokens_any_agent_gets_in() -> None:
    check = node_main.token_check(settings())

    assert check(Hello(matp="0.1", agent=STRANGER)) is True


@pytest.mark.parametrize(
    ("values", "problems"),
    [
        ({}, []),
        ({"matp_host": "::1"}, []),
        ({"matp_host": "0.0.0.0"}, ["MATP_TOKENS is required", "MATP_TLS_CERT"]),
        ({"matp_host": "127.0.0.2"}, ["MATP_TOKENS is required", "MATP_TLS_CERT"]),
        (
            {
                "matp_host": "0.0.0.0",
                "matp_tokens": {GATEWAY.id: "a-long-enough-token"},
                "matp_tls_cert": "cert.pem",
                "matp_tls_key": "key.pem",
            },
            [],
        ),
        ({"matp_tokens": {GATEWAY.id: "short"}}, ["at least 16 characters"]),
        ({"matp_tokens": {GATEWAY.id: ""}}, ["at least 16 characters"]),
    ],
    ids=["loopback", "ipv6-loopback", "any", "other-127", "exposed-ok", "short", "empty"],
)
def test_exposure_problems(values: dict[str, Any], problems: list[str]) -> None:
    found = node_main.exposure_problems(settings(**values))

    assert len(found) == len(problems)
    for expected, actual in zip(problems, found, strict=True):
        assert expected in actual
