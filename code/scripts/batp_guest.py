"""A guest's own AI agent that speaks BATP to the node itself (#252): `make guest MESSAGE="…"`.

Claude reaches the merchants' agents through the gateway, a bridge. This agent has a model of
its own (the same OPENAI_API_KEY) and two tools over `BatpClient`, `discover` and `talk`: two AIs
talking in BATP, with every frame printed and `HELLO`'s token masked. Only the person at the
terminal confirms a booking: the script sends `confirm` after they type yes, never the model.

    uv run python scripts/batp_guest.py "Table for 2 tonight at 8pm, somewhere vegetarian"

Then answer the agent's questions at `you>`; an empty line ends. Without `BATP_URL` it talks
to `batp://127.0.0.1:4710` (`make node`). On the team server, port 4710 stays on loopback and
SSH forwarding is off, so it runs there (documentation/runbooks/demo.md). When the node has
`BATP_TOKENS`, this agent needs its own entry, `urn:batp:agent:sesame-guest`: the script reads
its token there, and sends it to a node on this machine only, since `batp://` has no TLS.

A node that can't be reached, a lost session or a model outage are printed, never a traceback;
a `TALK` lost under its answer is sent once more with the same key, and the node replays it.
"""

import argparse
import asyncio
import contextlib
import json
import sys
import threading
import unicodedata
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from openai import Timeout
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.times import clock_line
from batp.client import BatpClient, BatpRejected, parse_batp_url
from batp.codec import encode
from batp.messages import PROTOCOL_VERSION, AgentIdentity, Hello, Intent
from core.config import Settings
from core.errors import AgentUnavailable
from core.llm import (
    LLMClient,
    LLMUnavailable,
    Message,
    OpenAIClient,
    ToolCall,
    ToolSpec,
    openai_connection,
)
from core.models import Slug, TalkMessage, TalkOutput

GUEST = AgentIdentity(
    id="urn:batp:agent:sesame-guest",
    name="Sésame's guest agent (demo)",
    kind="user-agent",
    operator="merchant-agents",
)
NODE_URL = "batp://127.0.0.1:4710"
# `batp://` has no TLS: the token goes only to a node on this machine.
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
MAX_STEPS = 8
# One request with its answer: above the node's 25 s for `TALK`, whose 503 then arrives first.
EXCHANGE_SECONDS = 30.0
LLM_TIMEOUT = Timeout(60, connect=5)
YES = frozenset({"yes", "y", "oui", "ja"})
# What the script, not the model, sends with `confirm` once the person said yes.
CONFIRMING = "Yes, the guest confirms."

PROMPT = """\
You are one guest's personal AI agent. You find restaurants and talk with each restaurant's own \
AI agent for the guest, over the BATP protocol.

{clock}
- `discover` finds restaurants; `talk` sends one message to a restaurant's agent and gives you \
its answer. Keep talking to the same restaurant: the conversation carries on.
- Say what the guest wants: the party size, the day and time, their name when booking, and \
their preferences.
- You never confirm a booking yourself. When the restaurant's agent proposes one, the guest is \
asked at their terminal, and you are told what they answered and what happened.
- Tell the guest briefly what the restaurant said, with any link it gives. Use their language.
"""


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiscoverArgs(_Args):
    query: str = Field(max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=32)


class TalkArgs(_Args):
    merchant: Slug
    message: TalkMessage


SPECS = (
    ToolSpec(
        "discover",
        "Restaurants matching a query (a name, a city, a kind of food) and tags such as "
        "vegetarian or terrace.",
        DiscoverArgs.model_json_schema(),
    ),
    ToolSpec(
        "talk",
        "Send one message to the restaurant's own AI agent, by its slug from discover.",
        TalkArgs.model_json_schema(),
    ),
)

Ask = Callable[[str], Awaitable[str]]
Show = Callable[[str], None]


class GuestAgent:
    """The guest's side of the conversation: the model, the node, and the person who confirms."""

    def __init__(
        self,
        llm: LLMClient,
        client: BatpClient,
        ask: Ask,
        show: Show,
        tz: ZoneInfo,
        *,
        token_set: bool = False,
    ) -> None:
        self._llm = llm
        self._client = client
        self._ask = ask
        self._show = show
        self._tz = tz
        self._token_set = token_set
        self._history: list[Message] = []
        # The conversation with each merchant's agent: code keeps it, the model never sees it.
        self._conversations: dict[str, str] = {}
        self._said_hello = False

    async def turn(self, text: str) -> str:
        """The person's message, then the model's calls until it answers them in words."""
        self._history.append({"role": "user", "content": text})
        for _ in range(MAX_STEPS):
            try:
                reply = await self._llm.respond(
                    instructions=PROMPT.format(clock=clock_line(datetime.now(self._tz))),
                    tools=SPECS,
                    history=self._history,
                    on_text=_ignore,
                )
            except LLMUnavailable as error:
                # What the merchant's agent did is already printed, by code (`_talk`).
                self._show(f"\nagent> (the model is unavailable: {error}; try again)\n")
                return ""
            self._history.append(reply.as_message())
            if not reply.tool_calls:
                self._show(f"\nagent> {reply.text}\n")
                return reply.text
            for call in reply.tool_calls:
                content = await self._run(call)
                self._history.append(
                    {"role": "tool", "call_id": call.id, "content": content, "is_error": False}
                )
        self._show("\nagent> (stopped: too many steps)\n")
        return ""

    async def _run(self, call: ToolCall) -> str:
        try:
            if call.name == "discover":
                args = DiscoverArgs.model_validate_json(call.arguments)
                params = {"query": args.query, "filters": {"tags": args.tags}}
                return _json(await self._exchange("DISCOVER", None, params))
            if call.name == "talk":
                return _json(await self._talk(TalkArgs.model_validate_json(call.arguments)))
        except ValidationError as error:
            return _json({"error": "invalid_input", "message": str(error)[:300]})
        return _json({"error": "invalid_input", "message": f"No tool named {call.name!r}"})

    async def _talk(self, args: TalkArgs) -> dict[str, Any]:
        answer, output = await self._send(args.merchant, args.message)
        if output is None or output.pending is None:
            return answer
        pending = output.pending
        said = await self._ask(f"\n{_printable(pending.summary)}\nConfirm? [yes/no] ")
        if said.strip().lower() not in YES:
            return answer | {"guest_answer": "no: nothing was confirmed"}
        confirmed, done = await self._send(args.merchant, CONFIRMING, confirm=pending.id)
        if done is not None and done.reservation is not None:
            booking = done.reservation
            self._show(f"✓ {booking.id}: {booking.status}, {booking.party_size} people")
        return answer | {"guest_answer": "yes", "after_confirm": confirmed}

    async def _send(
        self, merchant: str, message: str, confirm: str | None = None
    ) -> tuple[dict[str, Any], TalkOutput | None]:
        """One `TALK`, sent again once with the same key if the session is lost under it: the
        node then replays its answer (PROTOCOL.md §7), so a lost `confirm` can't book twice."""
        params: dict[str, Any] = {"message": message, "idempotency_key": str(uuid4())}
        if merchant in self._conversations:
            params["conversation"] = self._conversations[merchant]
        if confirm is not None:
            params["confirm"] = confirm
        answer = await self._exchange("TALK", merchant, params, attempts=2)
        if "error" in answer:
            if answer["error"] == "not_found":
                # Expired or forgotten (a restart): the next message starts a new one.
                self._conversations.pop(merchant, None)
            return answer, None
        try:
            output = TalkOutput.model_validate(answer)
        except ValidationError:
            self._show("✗ The merchant's agent sent an answer outside the contract")
            return {"error": "agent_unavailable", "message": "Unreadable answer"}, None
        self._conversations[merchant] = output.conversation
        return answer, output

    async def _exchange(
        self, intent: Intent, merchant: str | None, params: dict[str, Any], attempts: int = 1
    ) -> dict[str, Any]:
        """One request, its frames printed: its result, or its error as the model reads it. A
        lost session or no answer in time is `agent_unavailable` too, after `attempts` sends."""
        for attempt in range(1, attempts + 1):
            try:
                answer = await self._frames(intent, merchant, params)
            except (AgentUnavailable, TimeoutError) as error:
                # `SessionLost` and `BatpRejected` (a wrong token: the node's reason) included.
                problem = str(error) or f"No answer within {EXCHANGE_SECONDS:.0f} s"
                self._show(f"✗ {intent}: {problem}")
                if isinstance(error, BatpRejected) or attempt == attempts:
                    return {"error": "agent_unavailable", "message": problem}
                continue
            if fallback := answer.get("fallback_url"):
                self._show(f"  Book on the restaurant's own page: {fallback}")
            return answer
        raise AssertionError("attempts must be at least 1")

    async def _frames(
        self, intent: Intent, merchant: str | None, params: dict[str, Any]
    ) -> dict[str, Any]:
        if not self._said_hello:
            token = "•••" if self._token_set else None
            hello = Hello(batp=PROTOCOL_VERSION, agent=GUEST, token=token)
            self._show(f"→ {_line(hello)}")
        async with asyncio.timeout(EXCHANGE_SECONDS):
            request, response = await self._client.exchange(intent, merchant, params)
        if not self._said_hello and self._client.welcome is not None:
            self._show(f"← {_line(self._client.welcome)}")
            self._said_hello = True
        self._show(f"→ {_line(request)}")
        self._show(f"← {_line(response)}")
        if response.error is not None:
            error = {"error": response.error.code, "message": response.error.message}
            return (
                error | {"fallback_url": response.error.fallback_url}
                if response.error.fallback_url
                else error
            )
        return response.result or {}


def _printable(text: str) -> str:
    """What the node sent, without the control characters a terminal would act on."""
    return "".join(c for c in text if c == "\n" or not unicodedata.category(c).startswith("C"))


def _line(frame: BaseModel) -> str:
    return encode(frame).decode().removesuffix("\n")


def _json(content: dict[str, Any]) -> str:
    return json.dumps(content, ensure_ascii=False)


async def _ignore(_: str) -> None:
    """The reply is printed whole, after the frames it took."""


class GuestSettings(Settings):
    """The model, `BATP_URL` and `BATP_TOKENS` from the environment and `.env`; no database."""

    database_url: str = ""


async def chat(agent: GuestAgent, first: str, ask: Ask) -> None:
    """The person's first message, then theirs at `you>` until an empty line."""
    message = first
    while message:
        await agent.turn(message)
        message = (await ask("you> ")).strip()


async def _input(prompt: str) -> str:
    """A line from the terminal, read on a daemon thread: Ctrl-C ends the script while it waits,
    where `asyncio.to_thread` would wait for the line first."""
    loop = asyncio.get_running_loop()
    line: asyncio.Future[str] = loop.create_future()

    def settle(text: str) -> None:
        if not line.done():
            line.set_result(text)

    def read() -> None:
        try:
            text = input(prompt)
        except EOFError:
            text = ""
        # The loop may have ended meanwhile.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(settle, text)

    threading.Thread(target=read, daemon=True).start()
    return await line


def cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A guest's own AI agent, speaking BATP.")
    parser.add_argument("message", help='what the guest wants, e.g. "Table for 2 tonight at 8pm"')
    parser.add_argument("--url", help=f"the node, batp://host[:port] (BATP_URL, else {NODE_URL})")
    return parser


async def run(argv: Sequence[str] | None = None) -> int:
    args = cli().parse_args(argv)
    settings = GuestSettings()
    connection = openai_connection(settings, LLM_TIMEOUT)
    if connection is None:
        print("The guest agent needs OPENAI_API_KEY.", file=sys.stderr)
        return 1
    llm = OpenAIClient(
        connection, settings.openai_model, reasoning_effort=settings.agent_reasoning_effort
    )
    host, port = parse_batp_url(args.url or settings.batp_url or NODE_URL)
    secret = settings.batp_tokens.get(GUEST.id)
    token = secret.get_secret_value() if secret else None
    if token is not None and host not in LOOPBACK:
        print(
            f"Not sending the token to {host}: batp:// has no TLS. Run this on the node's "
            "machine (documentation/runbooks/demo.md).",
            file=sys.stderr,
        )
        return 2
    async with BatpClient(host, port, GUEST, token=token, connect_timeout=5) as client:
        agent = GuestAgent(
            llm, client, _input, print, ZoneInfo(settings.merchant_tz), token_set=token is not None
        )
        await chat(agent, args.message, _input)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return asyncio.run(run(argv))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
