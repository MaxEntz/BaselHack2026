"""Interim `build_agent` until the real builder lands (#22).

Provisions the Le Petit Zinc fixture whatever the website, while emitting the five builder
steps, so that onboarding can be demoed and tested end to end. Swap it for
`builder.build.build_agent` once #22 is merged.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncEngine

from agent.provisioning import SlugTaken, deploy, generated_tools, provision
from core.models import BuildAgentInput, BuildAgentOutput, MerchantProfile, StepEvent

FIXTURE = Path(__file__).parents[4] / "tests" / "fixtures" / "le-petit-zinc.json"
ASSUMPTIONS = ("Bookings last 120 minutes (default).",)

EmitStep = Callable[[StepEvent], Awaitable[None]]
StepName = Literal["crawl", "extract", "validate", "provision", "deploy"]


def fixture_build_agent(
    engine: AsyncEngine, mcp_url: str, *, pause_s: float = 0.4
) -> Callable[[BuildAgentInput, EmitStep], Awaitable[BuildAgentOutput]]:
    async def build_agent(arguments: BuildAgentInput, emit: EmitStep) -> BuildAgentOutput:
        profile = MerchantProfile.model_validate_json(FIXTURE.read_text())

        async def run(name: StepName, work: Callable[[], Awaitable[str]]) -> None:
            started = time.perf_counter()
            await emit(StepEvent(step=name, status="running"))
            detail = await work()
            duration = round((time.perf_counter() - started) * 1000)
            await emit(StepEvent(step=name, status="done", detail=detail, duration_ms=duration))

        def pause(detail: str) -> Callable[[], Awaitable[str]]:
            async def work() -> str:
                await asyncio.sleep(pause_s)
                return detail

            return work

        async def provisioned() -> str:
            try:
                await provision(profile, engine=engine)
            except SlugTaken:
                # Onboarding again in the demo: the agent already exists, keep it.
                return "Your agent already existed: kept as is."
            return f"Created {len(profile.tables)} tables with {profile.seats} seats."

        async def deployed() -> str:
            await deploy(profile.slug, engine=engine)
            return "Your agent is live."

        await run("crawl", pause("Read the homepage, menu, hours, contact and booking pages."))
        await run("extract", pause("Combined the website with your description."))
        await run("validate", pause("The profile is complete and consistent."))
        await run("provision", provisioned)
        await run("deploy", deployed)
        return BuildAgentOutput(
            profile=profile,
            status="live",
            assumptions=ASSUMPTIONS,
            tools=tuple(generated_tools(profile)),
            mcp_url=mcp_url,
        )

    return build_agent
