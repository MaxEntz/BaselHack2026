"""LLM client behind the assistant: a provider-neutral interface and its OpenAI implementation.

The conversation is stored in a neutral format (`Message`), so the tool-use loop never
touches a provider's types. Messages:

    {"role": "user", "content": str}
    {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "arguments"}]}
    {"role": "tool", "call_id": str, "content": str, "is_error": bool}
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import openai
from openai import AsyncOpenAI

log = logging.getLogger("merchant_platform.llm")

Message = dict[str, Any]
OnText = Callable[[str], Awaitable[None]]

# JSON Schema string formats OpenAI's strict mode accepts (spike #4: `uri` is refused).
SUPPORTED_FORMATS = {
    "date-time", "time", "date", "duration", "email", "hostname", "ipv4", "ipv6", "uuid"
}  # fmt: skip


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    # JSON Schema of the arguments.
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    # JSON text, validated by the tool's Pydantic model.
    arguments: str


@dataclass(frozen=True)
class LLMReply:
    text: str
    tool_calls: tuple[ToolCall, ...] = field(default=())

    def as_message(self) -> Message:
        return {
            "role": "assistant",
            "content": self.text,
            "tool_calls": [
                {"id": call.id, "name": call.name, "arguments": call.arguments}
                for call in self.tool_calls
            ],
        }


class LLMUnavailable(Exception):
    """The provider failed (overload, rate limit, network) even after the retry."""


class LLMClient(Protocol):
    async def respond(
        self,
        *,
        instructions: str,
        tools: Sequence[ToolSpec],
        history: Sequence[Message],
        on_text: OnText,
    ) -> LLMReply:
        """One model call: streams text through `on_text`, returns the text and tool calls."""
        ...


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Adapt a Pydantic schema to OpenAI's strict mode (same rules as the spike #4 script).

    Strict mode rejects unsupported string formats and wants every property in `required`:
    fields with a `None` default are already nullable through `anyOf`, so they become
    required-but-nullable and their `default` is dropped. Pydantic still validates in full.
    """
    cleaned: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "default" or (key == "format" and value not in SUPPORTED_FORMATS):
            continue
        if key in ("properties", "$defs"):
            value = {name: strict_schema(sub) for name, sub in value.items()}
        elif key == "items" and isinstance(value, dict):
            value = strict_schema(value)
        elif key == "anyOf":
            value = [strict_schema(sub) for sub in value]
        cleaned[key] = value
    if "properties" in cleaned:
        cleaned["required"] = list(cleaned["properties"])
    return cleaned


def to_openai_input(history: Sequence[Message]) -> list[dict[str, Any]]:
    """Neutral messages → Responses API input items."""
    items: list[dict[str, Any]] = []
    for message in history:
        role = message["role"]
        if role == "user":
            items.append({"role": "user", "content": message["content"]})
        elif role == "assistant":
            if message["content"]:
                items.append({"role": "assistant", "content": message["content"]})
            items.extend(
                {
                    "type": "function_call",
                    "call_id": call["id"],
                    "name": call["name"],
                    "arguments": call["arguments"],
                }
                for call in message.get("tool_calls", ())
            )
        elif role == "tool":
            output = message["content"]
            if message.get("is_error"):
                output = f"Error: {output}"
            items.append(
                {"type": "function_call_output", "call_id": message["call_id"], "output": output}
            )
    return items


# Worth one retry: the provider is overloaded, rate-limiting or unreachable.
TRANSIENT = (
    openai.RateLimitError,
    openai.InternalServerError,
    openai.APIConnectionError,
)


class OpenAIClient:
    """Responses API, streamed, with strict function tools and one retry on transient errors."""

    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        *,
        reasoning_effort: str | None = None,
        retry_delay_s: float = 2.0,
    ) -> None:
        self._client = client
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._retry_delay_s = retry_delay_s

    async def respond(
        self,
        *,
        instructions: str,
        tools: Sequence[ToolSpec],
        history: Sequence[Message],
        on_text: OnText,
    ) -> LLMReply:
        request: dict[str, Any] = {
            "model": self._model,
            "instructions": instructions,
            "input": to_openai_input(history),
            "tools": [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": strict_schema(tool.parameters),
                    "strict": True,
                }
                for tool in tools
            ],
            # The history is ours: nothing needs to be kept on OpenAI's side.
            "store": False,
        }
        if self._reasoning_effort:
            request["reasoning"] = {"effort": self._reasoning_effort}

        for attempt in range(2):
            streamed = False
            try:
                async with self._client.responses.stream(**request) as stream:
                    async for event in stream:
                        if event.type == "response.output_text.delta":
                            streamed = True
                            await on_text(event.delta)
                    response = await stream.get_final_response()
            except TRANSIENT as error:
                # Retrying after text reached the merchant would repeat it.
                if attempt or streamed:
                    raise LLMUnavailable(type(error).__name__) from error
                log.warning("llm_retry", extra={"error": type(error).__name__})
                await asyncio.sleep(self._retry_delay_s)
                continue
            except openai.OpenAIError as error:
                raise LLMUnavailable(type(error).__name__) from error
            calls = tuple(
                ToolCall(id=item.call_id, name=item.name, arguments=item.arguments)
                for item in response.output
                if item.type == "function_call"
            )
            return LLMReply(text=response.output_text, tool_calls=calls)
        raise AssertionError("unreachable")
