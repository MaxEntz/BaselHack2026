"""Studio's requests through the assistant's loop (#152): a scripted model calls each tool, and
the tests check its effect and the card the merchant sees."""

import json
from datetime import date, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest

from agent.service import AgentService
from builder.look import DEFAULT_LOOK, reads_on, rgb_of, titled
from core.errors import PartyTooLarge
from core.models import (
    BuildAgentInput,
    BuildAgentOutput,
    ChatEvent,
    Look,
    MerchantProfile,
    Reservation,
    Rules,
    Slot,
)
from merchant_platform.assistant import Assistant, system_prompt
from merchant_platform.overview import MerchantRecord
from merchant_platform.sessions import Session
from merchant_platform.tools import AdminTools
from tests.unit.test_assistant import NOW, PROFILE, SLUG, TODAY, TZ, ScriptedLLM, call

AT = datetime(2026, 10, 31, 20, 0, tzinfo=ZoneInfo("Europe/Zurich"))


class StudioAgent:
    """The service calls Studio's tools make."""

    def __init__(self) -> None:
        self.rules = Rules(
            max_party_size=6,
            slot_length_min=120,
            fallback_url="https://zinc.example/reservation.html",  # type: ignore[arg-type]
            cancellation_policy=None,
            tools=(),
        )
        self.booked: list[tuple[datetime, int, str, str]] = []

    async def get_rules(self, slug: str) -> Rules:
        return self.rules

    async def update_rules(
        self, slug: str, max_party_size: int, fallback_url: str, cancellation_policy: str | None
    ) -> Rules:
        self.rules = self.rules.model_copy(
            update={"max_party_size": max_party_size, "cancellation_policy": cancellation_policy}
        )
        return self.rules

    async def get_availability(
        self, slug: str, day: date, party_size: int, time: str | None = None
    ) -> list[Slot]:
        if party_size > self.rules.max_party_size:
            raise PartyTooLarge(f"Groups up to {self.rules.max_party_size}")
        return [
            Slot(starts_at=AT.replace(hour=h, minute=m), table_label="T1")
            for h, m in ((19, 30), (20, 0), (20, 30))
        ]

    async def create_reservation(
        self,
        slug: str,
        starts_at: datetime,
        party_size: int,
        guest_name: str,
        idempotency_key: str,
        via: str,
    ) -> Reservation:
        self.booked.append((starts_at, party_size, guest_name, via))
        return Reservation(
            id="R-042",
            merchant=slug,
            starts_at=starts_at,
            party_size=party_size,
            table_label="T1",
            status="confirmed",
            guest_name=guest_name,
            via=via,  # type: ignore[arg-type]
        )


class Studio:
    def __init__(self, llm: ScriptedLLM, *, status: str = "live", look: Look | None = None):
        self.agent = StudioAgent()
        self.status = status
        self.look = look
        self.saved: list[Look] = []
        self.events: list[ChatEvent] = []

        async def merchant(slug: str) -> MerchantRecord:
            return MerchantRecord(
                slug, "Le Petit Zinc", "Mulhouse", "€€", (), "https://zinc.example/",
                self.status, self.look,  # type: ignore[arg-type]
            )  # fmt: skip

        async def save_look(slug: str, look: Look) -> None:
            self.saved.append(look)
            self.look = look

        async def site_look(slug: str) -> Look:
            return DEFAULT_LOOK.model_copy(update={"accent": "#8f2d2a"})

        async def build(arguments: BuildAgentInput, emit: Any) -> BuildAgentOutput:
            raise AssertionError("no build here")

        async def profiles(slug: str) -> MerchantProfile:
            return PROFILE

        tools = AdminTools(
            cast(AgentService, self.agent),
            build,
            TZ,
            today=lambda: TODAY,
            merchant=merchant,
            save_look=save_look,
            site_look=site_look,
        )

        async def statuses(slug: str) -> Any:
            return self.status

        self.assistant = Assistant(llm, tools, profiles, TZ, statuses=statuses, clock=lambda: NOW)

    async def turn(self, message: str) -> list[dict[str, Any]]:
        session = Session(id="s")
        session.merchant_slug = SLUG

        async def emit(event: ChatEvent) -> None:
            self.events.append(event)

        await self.assistant(session, message, emit)
        return [json.loads(event.model_dump_json()) for event in self.events]


def cards(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e["data"] for e in events if e["type"] == "card" and e["kind"] == kind]


def said(llm: ScriptedLLM) -> str:
    """What the tool told the model: the tool message of the turn's last call."""
    [message] = [m for m in llm.calls[-1]["history"] if m["role"] == "tool"]
    return str(message["content"])


async def test_update_rules_saves_the_policy_and_shows_the_change() -> None:
    llm = ScriptedLLM(
        ([], [call("update_rules", cancellation_policy="Free up to 2 hours before")]),
        (["Done."], []),
    )
    studio = Studio(llm)

    events = await studio.turn("Cancellation policy: free up to 2 hours before")

    assert studio.agent.rules.cancellation_policy == "Free up to 2 hours before"
    assert cards(events, "diff") == [
        {"label": "Cancelling", "before": None, "after": "Free up to 2 hours before", "view": None}
    ]
    assert "Saved. Claude reads this before it cancels for a guest." in said(llm)


@pytest.mark.parametrize(
    ("party", "answer"),
    [
        (2, "An agent asking for 2 gets 3 times: 19:30, 20:00 and 20:30."),
        (9, "An agent asking for 9 gets a 413 with the link to your booking page"),
    ],
)
async def test_try_request_says_what_an_agent_gets(party: int, answer: str) -> None:
    llm = ScriptedLLM(([], [call("try_request", party_size=party)]), (["Done."], []))

    events = await Studio(llm).turn(f"A table for {party}?")

    assert cards(events, "confirmation")[0]["text"].startswith(answer)


async def test_while_hidden_agents_get_a_404_and_no_test_booking_is_made() -> None:
    llm = ScriptedLLM(
        ([], [call("try_request", party_size=2), call("send_test_booking", call_id="t2")]),
        (["Publish first."], []),
    )
    studio = Studio(llm, status="building")

    events = await studio.turn("Try it, and send a test booking")

    texts = [c["text"] for c in cards(events, "confirmation")]
    assert texts[0].startswith("Agents get a 404 until you publish.")
    assert (
        texts[1] == "Agents get a 404 until you publish. Do that first, and I'll send one through."
    )
    assert studio.agent.booked == []


async def test_send_test_booking_books_as_an_agent_would() -> None:
    llm = ScriptedLLM(([], [call("send_test_booking")]), (["Sent."], []))
    studio = Studio(llm)

    events = await studio.turn("Send a test booking")

    assert studio.agent.booked == [(AT.replace(hour=19, minute=30), 2, "Test booking", "claude")]
    assert cards(events, "confirmation")[0]["text"].startswith("Sent: R-042,")


async def test_update_look_sets_the_main_colour_and_keeps_text_on_it_readable() -> None:
    llm = ScriptedLLM(([], [call("update_look", accent="#2450c8")]), (["Done."], []))
    studio = Studio(llm, look=DEFAULT_LOOK)

    events = await studio.turn("Change the main colour to #2450c8")

    [saved] = studio.saved
    assert saved.accent == "#2450c8"
    assert saved.bg == DEFAULT_LOOK.bg
    assert cards(events, "diff") == [
        {"label": "Main colour", "before": "#616c48", "after": "#2450c8", "view": "manage"}
    ]
    assert "#2450c8 is your main colour now on the management site." in said(llm)


async def test_a_second_colour_the_text_cant_read_on_keeps_and_the_rest_gives_way() -> None:
    # #209's case: near-black text reads on the green; on the new navy it would read at 1.6:1.
    look = DEFAULT_LOOK.model_copy(
        update={"bg": "#f7f2e8", "accent": "#4f9a4a", "accent_2": "#b86a3c", "on_accent": "#111111"}
    )
    llm = ScriptedLLM(([], [call("update_look", accent_2="#1f3a5f")]), (["Done."], []))
    studio = Studio(llm, look=look)

    events = await studio.turn("Make the second colour #1f3a5f")

    [saved] = studio.saved
    assert (saved.accent_2, saved.on_accent) == ("#1f3a5f", "#ffffff")
    assert saved.accent != "#4f9a4a"
    assert reads_on(rgb_of(saved.on_accent), [rgb_of(saved.accent), rgb_of(saved.accent_2)])
    assert [card["label"] for card in cards(events, "diff")] == [
        "Second colour",
        "Main colour",
        "Buttons' text",
    ]
    text = said(llm)
    assert "#1f3a5f is your second colour now on the management site." in text
    # The green, at 3.1:1, darkens for the titles first; white then reads on both colours.
    assert (
        f"Your main colour is {saved.accent} now, so your titles read on your background." in text
    )
    assert "Your buttons' text is white now, so it reads on both colours." in text


async def test_a_second_colour_the_text_cant_read_on_is_adjusted_and_said() -> None:
    # The cream text reads on the olive, not on this red (3.7:1): the red darkens under it.
    llm = ScriptedLLM(([], [call("update_look", accent_2="#c0604a")]), (["Done."], []))
    studio = Studio(llm, look=DEFAULT_LOOK)

    await studio.turn("Make the second colour #c0604a")

    [saved] = studio.saved
    assert (saved.accent, saved.on_accent) == (DEFAULT_LOOK.accent, DEFAULT_LOOK.on_accent)
    assert saved.accent_2 != "#c0604a"
    assert reads_on(rgb_of(saved.on_accent), [rgb_of(saved.accent), rgb_of(saved.accent_2)])
    assert (
        f"#c0604a would make your buttons' text hard to read, so I adjusted it to {saved.accent_2}."
        in said(llm)
    )


async def test_a_main_colour_stored_before_209_is_set_as_titles_draw_it() -> None:
    # A green at 3.1:1 on the page: the management site darkens it for its titles, and the
    # near-black text checked on the stored green would no longer read there.
    look = DEFAULT_LOOK.model_copy(
        update={
            "bg": "#f7f2e8",
            "surface": "#fffaf0",
            "accent": "#4f9a4a",
            "accent_2": "#b86a3c",
            "on_accent": "#111111",
        }
    )
    llm = ScriptedLLM(([], [call("update_look", accent_2="#aa3c28")]), (["Done."], []))
    studio = Studio(llm, look=look)

    await studio.turn("Make the second colour #aa3c28")

    [saved] = studio.saved
    bg, surface = rgb_of(saved.bg), rgb_of(saved.surface)
    assert titled(rgb_of(saved.accent), bg, surface) == rgb_of(saved.accent) != rgb_of("#4f9a4a")
    assert saved.accent_2 == "#aa3c28"
    assert reads_on(rgb_of(saved.on_accent), [rgb_of(saved.accent), rgb_of(saved.accent_2)])
    assert (
        f"Your main colour is {saved.accent} now, so your titles read on your background."
        in said(llm)
    )


async def test_the_other_colour_gives_way_and_is_said() -> None:
    # A look stored before #209: the cream text read at 3.9:1 on the blue. A new main colour
    # keeps the cream text, and the blue darkens under it.
    look = DEFAULT_LOOK.model_copy(update={"accent_2": "#0078f0"})
    llm = ScriptedLLM(([], [call("update_look", accent="#000000")]), (["Done."], []))
    studio = Studio(llm, look=look)

    events = await studio.turn("Make the main colour black")

    [saved] = studio.saved
    assert (saved.accent, saved.on_accent) == ("#000000", DEFAULT_LOOK.on_accent)
    assert saved.accent_2 not in ("#0078f0",)
    assert reads_on(rgb_of(saved.on_accent), [rgb_of(saved.accent), rgb_of(saved.accent_2)])
    assert [card["label"] for card in cards(events, "diff")] == ["Main colour", "Second colour"]
    assert (
        f"Your second colour is {saved.accent_2} now, so your buttons' text reads on it too."
        in said(llm)
    )


async def test_a_new_main_colour_keeps_the_text_that_reads_on_the_second() -> None:
    # #209: swapping the cream text for near-black on a yellow would break it on the brick.
    llm = ScriptedLLM(([], [call("update_look", accent="#f2d16b")]), (["Done."], []))
    studio = Studio(llm, look=DEFAULT_LOOK)

    await studio.turn("Make the main colour #f2d16b")

    [saved] = studio.saved
    assert saved.on_accent == DEFAULT_LOOK.on_accent
    assert saved.accent != "#f2d16b"
    assert reads_on(rgb_of(saved.on_accent), [rgb_of(saved.accent), rgb_of(saved.accent_2)])


async def test_a_faint_colour_is_adjusted_and_the_merchant_told() -> None:
    llm = ScriptedLLM(([], [call("update_look", accent="#e8e6dd")]), (["Done."], []))
    studio = Studio(llm, look=DEFAULT_LOOK)

    await studio.turn("Make it #e8e6dd")

    [saved] = studio.saved
    assert saved.accent != "#e8e6dd"
    assert f"#e8e6dd is too faint on your background, so I adjusted it to {saved.accent}." in said(
        llm
    )


async def test_headings_corners_and_back_to_the_sites_look() -> None:
    llm = ScriptedLLM(
        ([], [call("update_look", display_font="serif", corners="rounded")]),
        (["Done."], []),
        ([], [call("update_look", call_id="r", reset=True)]),
        (["Back."], []),
    )
    studio = Studio(llm, look=DEFAULT_LOOK)

    await studio.turn("Serif headings, rounder corners")
    await studio.turn("Back to the look from your site")

    first, second = studio.saved
    assert (first.display_font, first.corners) == ("serif", "rounded")
    assert second.accent == "#8f2d2a"  # read from the site again
    assert "Back to the look from your site." in said(llm)


async def test_show_view_can_ask_for_a_moment_of_the_day() -> None:
    llm = ScriptedLLM(([], [call("show_view", view="manage", moment="during")]), (["There."], []))

    events = await Studio(llm).turn("Show during service")

    assert cards(events, "view") == [{"view": "manage", "moment": "during"}]


async def test_show_view_switches_studios_canvas() -> None:
    llm = ScriptedLLM(([], [call("show_view", view="agent")]), (["There."], []))

    events = await Studio(llm).turn("Show me the agent site")

    assert cards(events, "view") == [{"view": "agent", "moment": None}]


def test_the_prompt_answers_what_agents_read_and_refuses_to_touch_the_site() -> None:
    prompt = system_prompt(NOW, PROFILE)

    assert "What agents read:\n- get_availability:" in prompt
    assert "No. Sésame only reads your public site." in prompt
    assert "Your agent site has no interface, so there's nothing to colour." in prompt
