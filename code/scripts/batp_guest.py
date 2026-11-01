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
its token there.
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from openai import Timeout
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.times import clock_line
from batp.client import BatpClient, parse_batp_url
from batp.codec import encode
from batp.messages import PROTOCOL_VERSION, AgentIdentity, Hello, Intent
from core.config import Settings
from core.llm import LLMClient, Message, OpenAIClient, ToolCall, ToolSpec, openai_connection
from core.models import Slug, TalkMessage

GUEST = AgentIdentity(
    id="urn:batp:agent:sesame-guest",
    name="Sésame's guest agent (demo)",
    kind="user-agent",
    operator="merchant-agents",
)
NODE_URL = "batp://127.0.0.1:4710"
MAX_STEPS = 8
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
            reply = await self._llm.respond(
                instructions=PROMPT.format(clock=clock_line(datetime.now(self._tz))),
                tools=SPECS,
                history=self._history,
                on_text=_ignore,
            )
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
        answer = await self._send(args.merchant, args.message)
        pending = answer.get("pending")
        if not pending:
            return answer
        said = (await self._ask(f"\n{pending['summary']}\nConfirm? [yes/no] ")).strip().lower()
        if said not in YES:
            return answer | {"guest_answer": "no: nothing was confirmed"}
        confirmed = await self._send(args.merchant, CONFIRMING, confirm=pending["id"])
        return answer | {"guest_answer": "yes", "after_confirm": confirmed}

    async def _send(
        self, merchant: str, message: str, confirm: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"message": message, "idempotency_key": str(uuid4())}
        if merchant in self._conversations:
            params["conversation"] = self._conversations[merchant]
        if confirm is not None:
            params["confirm"] = confirm
        answer = await self._exchange("TALK", merchant, params)
        if "conversation" in answer:
            self._conversations[merchant] = answer["conversation"]
        elif answer.get("error") == "not_found":
            # Expired or forgotten (a restart): the next message starts a new one.
            self._conversations.pop(merchant, None)
        return answer

    async def _exchange(
        self, intent: Intent, merchant: str | None, params: dict[str, Any]
    ) -> dict[str, Any]:
        """One request, both frames printed: its result, or its error as the model reads it."""
        if not self._said_hello:
            token = "•••" if self._token_set else None
            hello = Hello(batp=PROTOCOL_VERSION, agent=GUEST, token=token)
            self._show(f"→ {_line(hello)}")
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
    try:
        return await asyncio.to_thread(input, prompt)
    except EOFError:
        return ""


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A guest's own AI agent, speaking BATP.")
    parser.add_argument("message", help='what the guest wants, e.g. "Table for 2 tonight at 8pm"')
    parser.add_argument("--url", help=f"the node, batp://host[:port] (BATP_URL, else {NODE_URL})")
    return parser


async def run(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
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
    async with BatpClient(host, port, GUEST, token=token, connect_timeout=5) as client:
        agent = GuestAgent(
            llm, client, _input, print, ZoneInfo(settings.merchant_tz), token_set=token is not None
        )
        await chat(agent, args.message, _input)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(argv))


if __name__ == "__main__":
    sys.exit(main())
