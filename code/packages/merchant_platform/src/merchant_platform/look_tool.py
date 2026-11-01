"""`update_look` (#152, #209): the management site's colours, headings' font and corners.

The colour rules are `builder.look.adjust_accents`'s; this tool says what they did, and shows
one change card per value it changed.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from builder.look import (
    CORNER_WORDS,
    DEFAULT_LOOK,
    FONT_WORDS,
    TEXT_ON,
    AccentChange,
    adjust_accents,
    hex_of,
)
from builder.look import describe as describe_look
from core.errors import AgentUnavailable, InvalidInput
from core.models import DiffCard, Look, UpdateLookInput
from merchant_platform.registry import RegistryLookup, SaveLook
from merchant_platform.sessions import Emit
from merchant_platform.specs import (
    ACCENT_LABELS,
    LOOK_RESET,
    NO_SITE_LOOK,
    NOT_AVAILABLE,
    Outcome,
    confirmation,
    diff,
)

# Reads the merchant website's own look again.
SiteLook = Callable[[str], Awaitable[Look | None]]

ROLES = {"accent": "main", "accent_2": "second"}
# Why the other accent moved (`AccentChange.why`).
GAVE_WAY = {
    "text": "your buttons' text reads on it too",
    "titles": "your titles read on your background",
}


class LookTool:
    def __init__(
        self, merchant: RegistryLookup | None, save: SaveLook | None, site: SiteLook | None
    ) -> None:
        self._merchant = merchant
        self._save = save
        self._site = site

    async def __call__(self, slug: str, arguments: UpdateLookInput, emit: Emit) -> Outcome:
        save = self._save
        if self._merchant is None or save is None:
            raise AgentUnavailable(NOT_AVAILABLE)
        record = await self._merchant(slug)
        if record is None:
            raise AgentUnavailable(NOT_AVAILABLE)
        current = record.look or DEFAULT_LOOK
        if arguments.reset:
            return await self._reset(slug, current, save)
        changes, texts, cards = _changes(current, arguments)
        if not changes:
            raise InvalidInput("Say what to change: a colour, the headings' font or the corners.")
        updated = current.model_copy(update=changes)
        await save(slug, updated)
        for card in cards[:-1]:
            await emit(card)
        return Outcome(
            {"shown_to_merchant": "change", "text": " ".join(texts), "look": updated.model_dump()},
            cards[-1],
        )

    async def _reset(self, slug: str, current: Look, save: SaveLook) -> Outcome:
        """Back to the look read from the merchant's website."""
        site = await self._site(slug) if self._site else None
        if site is None:
            return confirmation(NO_SITE_LOOK)
        await save(slug, site)
        return Outcome(
            {"shown_to_merchant": "change", "text": LOOK_RESET},
            diff("Look", describe_look(current), describe_look(site), view="manage"),
        )


def _changes(
    current: Look, arguments: UpdateLookInput
) -> tuple[dict[str, Any], list[str], list[DiffCard]]:
    """What to store, what to say, and a card per changed value: colours, then the headings'
    font, then the corners."""
    changes: dict[str, Any] = {}
    texts: list[str] = []
    cards: list[DiffCard] = []
    wanted = {
        role: getattr(arguments, role)
        for role in ACCENT_LABELS
        if getattr(arguments, role) is not None
    }
    if wanted:
        fit = adjust_accents(current, wanted)
        for change in fit.changes:
            changes[change.role] = change.after
            texts.append(_said(change))
            cards.append(
                diff(ACCENT_LABELS[change.role], change.before, change.after, view="manage")
            )
        if fit.on_accent != current.on_accent:
            changes["on_accent"] = fit.on_accent
            colour = "white" if fit.on_accent == hex_of(TEXT_ON[0]) else "near-black"
            texts.append(f"Your buttons' text is {colour} now, so it reads on both colours.")
            cards.append(diff("Buttons' text", current.on_accent, fit.on_accent, view="manage"))
    if arguments.display_font is not None:
        changes["display_font"] = arguments.display_font
        texts.append(f"Your headings use {FONT_WORDS[arguments.display_font].lower()} now.")
        cards.append(diff("Headings", current.display_font, arguments.display_font, view="manage"))
    if arguments.corners is not None:
        changes["corners"] = arguments.corners
        texts.append(f"{CORNER_WORDS[arguments.corners]} on the management site now.")
        cards.append(diff("Corners", current.corners, arguments.corners, view="manage"))
    return changes, texts, cards


def _said(change: AccentChange) -> str:
    role, asked, used = ROLES[change.role], change.asked, change.after
    if asked is None:
        return f"Your {role} colour is {used} now, so {GAVE_WAY[change.why]}."
    if change.why == "asked":
        return f"{used} is your {role} colour now on the management site."
    if change.why == "faint":
        return f"{asked} is too faint on your background, so I adjusted it to {used}."
    return f"{asked} would make your buttons' text hard to read, so I adjusted it to {used}."
