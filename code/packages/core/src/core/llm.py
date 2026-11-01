"""LLM client of the builder, the platform assistant and the merchants' agents: a
provider-neutral interface and its OpenAI implementation.

In `core`, so the BATP node's agents use it without the builder and its browser (#246).

The conversation is stored in a neutral format (`Message`), so the tool-use loop never
touches a provider's types. Messages:

    {"role": "user", "content": str}
    {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "arguments"}],
     "provider_items": [...]}
    {"role": "tool", "call_id": str, "content": str, "is_error": bool}

`provider_items` is opaque to the loop: what a client needs to resend with that message
(OpenAI: the encrypted reasoning items). Other clients ignore it.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import openai
from openai import AsyncOpenAI, Timeout

from core.config import Settings

log = logging.getLogger("core.llm")

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
    provider_items: tuple[dict[str, Any], ...] = field(default=())

    def as_message(self) -> Message:
        return {
            "role": "assistant",
            "content": self.text,
            "tool_calls": [
                {"id": call.id, "name": call.name, "arguments": call.arguments}
                for call in self.tool_calls
            ],
            "provider_items": list(self.provider_items),
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
        tool_choice: str | None = None,
    ) -> LLMReply:
        """One model call: streams text through `on_text`, returns the text and tool calls.

        `tool_choice` names a tool the model must call (the builder's extraction).
        """
        ...


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Adapt a Pydantic schema to OpenAI's strict mode (the rules spike #4 found).

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
    """Neutral messages → Responses API input items.

    Reasoning items go back only within the current turn (after the last user message): they
    help the model carry on between tool calls, and older ones are of no use to it.
    """
    users = [index for index, message in enumerate(history) if message["role"] == "user"]
    turn_start = users[-1] if users else 0
    items: list[dict[str, Any]] = []
    for index, message in enumerate(history):
        role = message["role"]
        if role == "user":
            items.append({"role": "user", "content": message["content"]})
        elif role == "assistant":
            if index > turn_start:
                items.extend(message.get("provider_items", ()))
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


def openai_connection(settings: Settings, timeout: Timeout) -> AsyncOpenAI | None:
    """The OpenAI connection of `OPENAI_API_KEY`, for the platform and the BATP node alike; None
    without a key. Retries are `OpenAIClient`'s (one, before any text), so the SDK makes none."""
    if settings.openai_api_key is None:
        return None
    return AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(), max_retries=0, timeout=timeout
    )


# Worth one retry: the provider is overloaded, rate-limiting or unreachable.
class _NotCompleted(Exception):
    """The stream ended without `response.completed`: it failed, was cut short, or just closed."""


TRANSIENT = (
    openai.RateLimitError,
    openai.InternalServerError,
    openai.APIConnectionError,
    _NotCompleted,
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
        tool_choice: str | None = None,
    ) -> LLMReply:
        request = self._request(instructions, tools, history, tool_choice)
        try:
            return await self._attempt(request, on_text, may_retry=True)
        except _Retry as retry:
            log.warning("llm_retry", extra={"error": retry.error})
            await asyncio.sleep(self._retry_delay_s)
        return await self._attempt(request, on_text, may_retry=False)

    def _request(
        self,
        instructions: str,
        tools: Sequence[ToolSpec],
        history: Sequence[Message],
        tool_choice: str | None,
    ) -> dict[str, Any]:
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
        if tool_choice:
            request["tool_choice"] = {"type": "function", "name": tool_choice}
        if self._reasoning_effort:
            request["reasoning"] = {"effort": self._reasoning_effort}
        if self._reasoning_effort != "none":
            # With store=False, reasoning comes back encrypted so that we can resend it.
            request["include"] = ["reasoning.encrypted_content"]
        return request

    async def _attempt(
        self, request: dict[str, Any], on_text: OnText, *, may_retry: bool
    ) -> LLMReply:
        """One streamed call. `_Retry` after a transient error when `may_retry` and no text
        reached the merchant yet (a retry would repeat it); `LLMUnavailable` for any other."""
        streamed = False
        ended = "closed"
        try:
            async with self._client.responses.stream(**request) as stream:
                async for event in stream:
                    if event.type == "response.output_text.delta":
                        streamed = True
                        await on_text(event.delta)
                    elif event.type in ("response.failed", "response.incomplete"):
                        ended = _why_not_completed(event.response)
                try:
                    response = await stream.get_final_response()
                except RuntimeError:
                    # What the SDK raises when no `response.completed` came.
                    log.warning("llm_stream_not_completed", extra={"ended": ended})
                    raise _NotCompleted(ended) from None
        except TRANSIENT as error:
            if may_retry and not streamed:
                raise _Retry(type(error).__name__) from error
            raise LLMUnavailable(type(error).__name__) from error
        except openai.OpenAIError as error:
            raise LLMUnavailable(type(error).__name__) from error
        calls = tuple(
            ToolCall(id=item.call_id, name=item.name, arguments=item.arguments)
            for item in response.output
            if item.type == "function_call"
        )
        reasoning = tuple(
            item.model_dump(mode="json", exclude_none=True)
            for item in response.output
            if item.type == "reasoning"
        )
        return LLMReply(text=response.output_text, tool_calls=calls, provider_items=reasoning)


def _why_not_completed(response: Any) -> str:
    """The API's own code for it, which holds no content: safe to log."""
    details, error = response.incomplete_details, response.error
    return (details.reason if details else None) or (error.code if error else None) or "unknown"


class _Retry(Exception):
    """A transient error before any text: worth the one retry."""

    def __init__(self, error: str) -> None:
        super().__init__(error)
        self.error = error
