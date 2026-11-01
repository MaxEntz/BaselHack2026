"""A scripted model for the merchant's AI agent (#246): its tests, and the node's (#249)."""

import asyncio
import json
from collections.abc import Awaitable, Sequence
from typing import Any
from uuid import uuid4

import pytest

from core.llm import LLMReply, Message, OnText, ToolCall, ToolSpec
from core.models import TalkOutput


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


class HangingLLM(ScriptedLLM):
    """After `hang()`, its next call never answers: the caller cuts the turn short."""

    def __init__(self) -> None:
        super().__init__()
        self.hanging = asyncio.Event()
        self._hang = False

    def hang(self) -> None:
        self._hang = True

    async def respond(self, **kwargs: Any) -> LLMReply:
        if not self._hang:
            return await super().respond(**kwargs)
        self._hang = False
        self.calls.append(kwargs)
        self.hanging.set()
        await asyncio.Event().wait()
        raise AssertionError("never answers")


async def cut_short(llm: HangingLLM, talking: Awaitable[TalkOutput]) -> None:
    """Cancels the turn while the model is called, as the node does past its deadline, on a
    closed session or a stop."""
    task = asyncio.ensure_future(talking)
    await llm.hanging.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
