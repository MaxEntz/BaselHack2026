"""A scripted model for the merchants' AI agent (#246), and a real BATP node serving it: the
agent's tests, the node's (#249) and the guest agent's (#252)."""

import json
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncEngine

from agent.service import PgAgentService
from agent.talk import MerchantAgent, registry_merchants
from batp.client import BatpClient
from batp.messages import AgentIdentity
from batp.server import NodeServer
from core.llm import LLMReply, Message, OnText, ToolCall, ToolSpec
from merchant_node.__main__ import NODE
from merchant_node.agents import MerchantAgents
from merchant_node.limits import TalkLimits
from merchant_node.requests_log import RequestLog

TZ = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 10, 31, 9, tzinfo=TZ)
FALLBACK = "http://localhost:8080/reservation.html"
TOKEN = "gw-token-for-the-talk-tests"
GATEWAY = AgentIdentity(
    id="urn:batp:agent:claude-gateway",
    name="Claude via the Sésame gateway",
    kind="user-agent",
    operator="merchant-agents",
)


def call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(f"call-{uuid4().hex[:6]}", name, json.dumps(arguments))


def says(text: str) -> LLMReply:
    return LLMReply(text=text)


def calls(*tool_calls: ToolCall) -> LLMReply:
    return LLMReply(text="", tool_calls=tool_calls)


class ScriptedLLM:
    """Replays its replies in order; records what each call was given."""

    def __init__(self, *replies: LLMReply | Exception) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def then(self, *replies: LLMReply | Exception) -> None:
        self.replies.extend(replies)

    async def respond(
        self,
        *,
        instructions: str,
        tools: Sequence[ToolSpec],
        history: Sequence[Message],
        on_text: OnText,
        tool_choice: str | None = None,
    ) -> LLMReply:
        self.calls.append(
            {"instructions": instructions, "tools": list(tools), "history": list(history)}
        )
        if not self.replies:
            raise AssertionError("unexpected model call")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class Node:
    """A node serving `MerchantAgents` with the agent on `llm` (none: no OPENAI_API_KEY), on
    `clock` (by default `NOW`; the e2e tests pass the real one)."""

    def __init__(
        self,
        engine: AsyncEngine,
        llm: ScriptedLLM | None,
        limits: TalkLimits | None = None,
        *,
        clock: Callable[[], datetime] = lambda: NOW,
    ) -> None:
        service = PgAgentService(engine, tz=TZ, clock=clock)
        talker = None
        if llm is not None:
            talker = MerchantAgent(llm, service, registry_merchants(engine), TZ, clock=clock)
        agents = MerchantAgents(engine, service, talker, limits)
        self.server = NodeServer(
            node=NODE,
            handle=agents,
            authenticate=lambda hello: hello.token == TOKEN,
            fallback_url=agents.fallback_url,
            record=RequestLog(engine),
        )

    def client(self, agent: AgentIdentity = GATEWAY) -> BatpClient:
        return BatpClient("127.0.0.1", self.server.port, agent, token=TOKEN)

    async def __aenter__(self) -> BatpClient:
        await self.server.start("127.0.0.1", 0)
        self._client = self.client()
        return await self._client.__aenter__()

    async def __aexit__(self, *exc: Any) -> None:
        await self._client.__aexit__(*exc)
        await self.server.stop()
