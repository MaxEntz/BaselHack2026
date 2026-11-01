"""How the protocol reaches a merchant's agent, whichever process it runs in.

`LocalAgentClient` calls the agent in the gateway's process. `BatpAgentClient` makes the gateway a
bridge: Claude still speaks MCP to it, and it speaks BATP to the node serving the merchant agents
(`documentation/PROTOCOL.md`, §12). Tools only know the `AgentClient` interface.
"""

import logging
from datetime import date
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from agent import discovery
from agent.service import AgentService, PgAgentService
from batp.client import BatpClient, parse_batp_url
from batp.messages import AgentIdentity, Intent
from core.config import Settings
from core.errors import AgentUnavailable
from core.models import (
    GetMyReservationsInput,
    GetMyReservationsOutput,
    Reservation,
    SearchFilters,
    SearchMerchantsInput,
    SearchMerchantsOutput,
    TalkInput,
    TalkOutput,
)
from gateway.fallback import batp_request

log = logging.getLogger("gateway.agent_client")

GATEWAY_AGENT = AgentIdentity(
    id="urn:batp:agent:claude-gateway",
    name="Claude via the Sésame gateway",
    kind="user-agent",
    operator="merchant-agents",
)
# The merchants' agents are LLMs served by the node (#249): this process has none to talk to.
NO_TALK_IN_PROCESS = "The merchant's agent only answers through a BATP node: set BATP_URL."


class AgentClient(Protocol):
    """The user operations of `AgentService`. Failures raise `core.errors.AgentError`."""

    async def search_merchants(
        self, query: str, near: str | None, day: date | None, filters: SearchFilters | None
    ) -> SearchMerchantsOutput: ...

    async def get_my_reservations(self, guest_name: str) -> tuple[list[Reservation], list[str]]: ...

    async def talk(self, turn: TalkInput) -> TalkOutput:
        """One message to the merchant's agent, and its answer (`TALK`, #245)."""
        ...

    async def close(self) -> None: ...


class LocalAgentClient:
    """In-process calls: the search and the guest's bookings. No talk: the merchant's agent is an
    LLM that only the node runs."""

    def __init__(self, service: AgentService, engine: AsyncEngine) -> None:
        self.service = service
        self.engine = engine

    async def search_merchants(
        self, query: str, near: str | None, day: date | None, filters: SearchFilters | None
    ) -> SearchMerchantsOutput:
        return await discovery.search(query, near, day, filters, engine=self.engine)

    async def get_my_reservations(self, guest_name: str) -> tuple[list[Reservation], list[str]]:
        return await self.service.find_guest_reservations(guest_name)

    async def talk(self, turn: TalkInput) -> TalkOutput:
        """Never: the gateway has no LLM. The guest gets the fallback URL; dev runs `make node`."""
        raise AgentUnavailable(NO_TALK_IN_PROCESS)

    async def close(self) -> None:
        pass


class BatpAgentClient:
    """The same operations over BATP. `params` and `result` are the contracts of `core.models`."""

    def __init__(self, client: BatpClient) -> None:
        self.client = client

    async def _request[M: BaseModel](
        self, intent: Intent, merchant: str | None, params: BaseModel, result: type[M]
    ) -> M:
        """The node's answer. Its errors and a lost session are `AgentError`s already."""
        request_id = f"r-{uuid4().hex[:12]}"
        batp_request.set(request_id)
        wire = params.model_dump(mode="json", exclude={"merchant"}, exclude_none=True)
        response = await self.client.request(intent, merchant, wire, request_id=request_id)
        try:
            return result.model_validate(response)
        except ValidationError as exc:
            raise AgentUnavailable(f"The BATP node sent an invalid {intent} result") from exc

    async def search_merchants(
        self, query: str, near: str | None, day: date | None, filters: SearchFilters | None
    ) -> SearchMerchantsOutput:
        params = SearchMerchantsInput(query=query, near=near, date=day, filters=filters)
        return await self._request("DISCOVER", None, params, SearchMerchantsOutput)

    async def get_my_reservations(self, guest_name: str) -> tuple[list[Reservation], list[str]]:
        params = GetMyReservationsInput(guest_name=guest_name)
        result = await self._request("RECALL", None, params, GetMyReservationsOutput)
        return list(result.reservations), list(result.notices)

    async def talk(self, turn: TalkInput) -> TalkOutput:
        """A `TALK`. The client never resends it on a lost session: the caller retries, same key."""
        return await self._request("TALK", turn.merchant, turn, TalkOutput)

    async def close(self) -> None:
        await self.client.close()


def make_agent_client(settings: Settings, engine: AsyncEngine) -> AgentClient:
    """Over BATP when `BATP_URL` is set, in this process otherwise."""
    if not settings.batp_url:
        log.warning("no BATP_URL: talk_to_merchant gets the fallback URL; run `make node`")
        service = PgAgentService(engine, tz=ZoneInfo(settings.merchant_tz))
        return LocalAgentClient(service, engine)
    host, port = parse_batp_url(settings.batp_url)
    token = settings.batp_token.get_secret_value() if settings.batp_token else None
    return BatpAgentClient(BatpClient(host, port, GATEWAY_AGENT, token))
