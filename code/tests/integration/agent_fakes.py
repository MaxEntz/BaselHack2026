"""A scripted model for the merchant's AI agent (#246): its tests, and the node's (#249)."""

import json
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from core.llm import LLMReply, Message, OnText, ToolCall, ToolSpec


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
