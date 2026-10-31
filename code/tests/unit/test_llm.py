"""OpenAI adapter against a mocked Responses API stream (no network)."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from openai import AsyncOpenAI

from merchant_platform.llm import (
    LLMUnavailable,
    Message,
    OpenAIClient,
    ToolSpec,
    strict_schema,
    to_openai_input,
)

BASE = {
    "id": "resp_1",
    "object": "response",
    "created_at": 0,
    "model": "gpt-test",
    "status": "in_progress",
    "output": [],
    "parallel_tool_calls": True,
    "tool_choice": "auto",
    "tools": [],
}
MESSAGE = {
    "type": "message",
    "id": "msg_1",
    "role": "assistant",
    "status": "completed",
    "content": [{"type": "output_text", "text": "Hello there", "annotations": []}],
}
CALL = {
    "type": "function_call",
    "id": "fc_1",
    "call_id": "call_1",
    "name": "get_stats",
    "arguments": '{"date":"2026-10-31"}',
    "status": "completed",
}
TEXT = {"output_index": 0, "content_index": 0, "item_id": "msg_1", "logprobs": []}
EVENTS: list[dict[str, Any]] = [
    {"type": "response.created", "response": BASE},
    {
        "type": "response.output_item.added",
        "output_index": 0,
        "item": {**MESSAGE, "status": "in_progress", "content": []},
    },
    {
        "type": "response.content_part.added",
        "output_index": 0,
        "content_index": 0,
        "item_id": "msg_1",
        "part": {"type": "output_text", "text": "", "annotations": []},
    },
    {"type": "response.output_text.delta", "delta": "Hello", **TEXT},
    {"type": "response.output_text.delta", "delta": " there", **TEXT},
    {"type": "response.output_text.done", "text": "Hello there", **TEXT},
    {"type": "response.output_item.done", "output_index": 0, "item": MESSAGE},
    {
        "type": "response.output_item.added",
        "output_index": 1,
        "item": {**CALL, "status": "in_progress", "arguments": ""},
    },
    {
        "type": "response.function_call_arguments.done",
        "output_index": 1,
        "item_id": "fc_1",
        "arguments": CALL["arguments"],
        "name": "get_stats",
    },
    {"type": "response.output_item.done", "output_index": 1, "item": CALL},
    {
        "type": "response.completed",
        "response": {**BASE, "status": "completed", "output": [MESSAGE, CALL]},
    },
]
TOOL = ToolSpec(
    "get_stats",
    "Figures for one day.",
    {
        "type": "object",
        "properties": {"date": {"type": "string", "format": "date"}},
        "additionalProperties": False,
    },
)


def stream(events: list[dict[str, Any]] = EVENTS) -> httpx.Response:
    body = "".join(
        f"event: {event['type']}\ndata: {json.dumps({**event, 'sequence_number': i})}\n\n"
        for i, event in enumerate(events)
    )
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def error(status: int) -> httpx.Response:
    return httpx.Response(status, json={"error": {"message": "nope", "type": "server_error"}})


class Api:
    """Answers each request with the next response in line, recording the requests."""

    def __init__(self, *responses: Callable[[], httpx.Response]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        return self.responses.pop(0)()

    def client(self) -> OpenAIClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        openai = AsyncOpenAI(api_key="sk-test", http_client=http, max_retries=0)
        return OpenAIClient(openai, "gpt-test", reasoning_effort="low", retry_delay_s=0)


async def respond(llm: OpenAIClient, deltas: list[str]) -> Any:
    async def on_text(delta: str) -> None:
        deltas.append(delta)

    return await llm.respond(
        instructions="Be brief.",
        tools=[TOOL],
        history=[{"role": "user", "content": "How busy?"}],
        on_text=on_text,
    )


async def test_streams_text_and_returns_tool_calls() -> None:
    api = Api(stream)
    deltas: list[str] = []

    reply = await respond(api.client(), deltas)

    assert deltas == ["Hello", " there"]
    assert reply.text == "Hello there"
    assert [(c.id, c.name, c.arguments) for c in reply.tool_calls] == [
        ("call_1", "get_stats", '{"date":"2026-10-31"}')
    ]
    request = api.requests[0]
    assert request["stream"] is True
    assert request["store"] is False
    assert request["reasoning"] == {"effort": "low"}
    assert request["instructions"] == "Be brief."
    assert request["tools"][0]["strict"] is True
    assert request["tools"][0]["parameters"]["required"] == ["date"]


REASONING = {
    "type": "reasoning",
    "id": "rs_1",
    "summary": [],
    "encrypted_content": "opaque==",
}


def stream_with_reasoning() -> httpx.Response:
    completed = {**BASE, "status": "completed", "output": [REASONING, MESSAGE, CALL]}
    return stream([*EVENTS[:-1], {"type": "response.completed", "response": completed}])


async def test_reasoning_items_are_requested_and_kept_for_the_next_call() -> None:
    api = Api(stream_with_reasoning)

    reply = await respond(api.client(), [])

    assert api.requests[0]["include"] == ["reasoning.encrypted_content"]
    assert reply.provider_items == (REASONING,)
    assert reply.as_message()["provider_items"] == [REASONING]


def test_reasoning_items_go_back_within_the_current_turn_only() -> None:
    earlier = {"type": "reasoning", "id": "rs_0", "summary": [], "encrypted_content": "old"}
    history: list[Message] = [
        {"role": "user", "content": "Block 21:00"},
        {"role": "assistant", "content": "Done.", "tool_calls": [], "provider_items": [earlier]},
        {"role": "user", "content": "Block 21:00 then show me tonight"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "name": "block_slot", "arguments": "{}"}],
            "provider_items": [REASONING],
        },
        {"role": "tool", "call_id": "c1", "content": "{}", "is_error": False},
    ]

    items = to_openai_input(history)

    assert earlier not in items
    assert items[3:5] == [
        REASONING,
        {"type": "function_call", "call_id": "c1", "name": "block_slot", "arguments": "{}"},
    ]


async def test_retries_once_on_overload() -> None:
    api = Api(lambda: error(503), stream)
    deltas: list[str] = []

    reply = await respond(api.client(), deltas)

    assert len(api.requests) == 2
    assert deltas == ["Hello", " there"]
    assert reply.text == "Hello there"


@pytest.mark.parametrize(
    ("responses", "attempts"),
    [((503, 503), 2), ((429, 500), 2), ((400,), 1), ((401,), 1)],
)
async def test_gives_up_as_unavailable(responses: tuple[int, ...], attempts: int) -> None:
    api = Api(*(lambda status=status: error(status) for status in responses))

    with pytest.raises(LLMUnavailable):
        await respond(api.client(), [])

    assert len(api.requests) == attempts


def test_history_becomes_responses_api_items() -> None:
    history: list[Message] = [
        {"role": "user", "content": "Cancel R-404"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "name": "cancel_reservation", "arguments": "{}"}],
        },
        {"role": "tool", "call_id": "c1", "content": "not_found: no R-404", "is_error": True},
        {"role": "assistant", "content": "I can't find it.", "tool_calls": []},
    ]

    assert to_openai_input(history) == [
        {"role": "user", "content": "Cancel R-404"},
        {"type": "function_call", "call_id": "c1", "name": "cancel_reservation", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "Error: not_found: no R-404"},
        {"role": "assistant", "content": "I can't find it."},
    ]


def test_strict_schema_requires_every_field_and_drops_unsupported_formats() -> None:
    schema = {
        "type": "object",
        "properties": {
            "website": {"type": "string", "format": "uri"},
            "day": {"type": "string", "format": "date"},
            "note": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
        },
        "additionalProperties": False,
    }

    assert strict_schema(schema) == {
        "type": "object",
        "properties": {
            "website": {"type": "string"},
            "day": {"type": "string", "format": "date"},
            "note": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        },
        "additionalProperties": False,
        "required": ["website", "day", "note"],
    }
