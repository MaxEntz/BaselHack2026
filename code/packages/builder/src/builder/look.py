"""A merchant's `Look`, from its homepage's computed styles (#149).

Pure code, no LLM (ADR-0003): the crawler samples the page (`probes.LOOK`), and these rules
give each colour a role. The contrast guard is the mockup's `readable()`
(documentation/design/poc-v10/sesame-studio.html). The colours' names and the flags are for the
review card (#136).
"""

import re
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from builder.words import joined
from core.models import BodyFont, BuildFlag, DisplayFont, Look

RGB = tuple[int, int, int]
Corners = Literal["organic", "rounded", "square"]
Texture = Literal["leather", "none"]

# #150: no look means La Graine Verte's, the management mockup's own tokens. Its olive is the
# mockup's #727f55 made darker until its button text reads at 4.5:1 (#209), as `read_look`
# gives it from the site and the management site's tokens.css has it (#153).
DEFAULT_LOOK = Look(
    bg="#edebe2",
    surface="#f8f6ef",
    line="#d5d2c3",
    ink="#47522f",
    muted="#5f6a45",
    accent="#616c48",
    accent_2="#935046",
    on_accent="#f4f2ea",
    display_font="Fraunces Soft",
    body_font="Karla",
    corners="organic",
    radius_px=30,
    texture="leather",
)
NO_LOOK = BuildFlag(
    title="I couldn't read your site's colours.",
    detail="The colours will be plain until you pick some.",
)

# A colour is vivid from this RGB spread on: olive (#727f55, 42) is, cream (#f8f6ef, 9) isn't.
VIVID = 40
# accent_2 must differ in hue from accent by this much: brick isn't a darker olive.
OTHER_HUE = 30
# A surface or a texture counts from this share of the page.
SURFACE_SHARE = 0.02
BLOB_SHARE = 0.05
ACCENT_CONTRAST = 3.0
TEXT_CONTRAST = 4.5
# Under this luminance a page is dark, and a colour lightens to read on it (look.ts's `isDark`).
DARK = 0.18
# The management site also sets text on each accent made this much darker (--olive-band,
# --brick-deep, #150).
SHADE = 0.08
# The mockup's contrast guard moves a colour this share of the way to black or white per step, at
# most this many steps (`readable`, `titled`, `under`).
STEP = 0.08
MAX_STEPS = 40
# Button text when the site's own doesn't read: white or near-black.
TEXT_ON: tuple[RGB, RGB] = ((255, 255, 255), (17, 17, 17))
MAX_RADIUS = 32
ACCENTS = ("accent", "accent_2")

FONTS_BUNDLED: dict[str, str] = {"fraunces soft": "Fraunces Soft", "fraunces": "Fraunces Soft"}
BODY_BUNDLED: dict[str, str] = {"karla": "Karla"}
SERIF = re.compile(r"^serif$|^ui-serif$|georgia|times|garamond|palatino|iowan|baskerville|antiqua")
SANS = re.compile(
    r"^sans-serif$|system-ui|helvetica|arial|segoe|roboto|^inter$|-apple-system|karla|verdana"
)
GENERIC = {"serif", "sans-serif", "system-ui", "-apple-system", "ui-sans-serif", "ui-serif"}

# The names the review card gives colours: the nearest of these.
NAMES: dict[str, RGB] = {
    "olive": (114, 127, 85),
    "brick": (147, 80, 70),
    "cream": (248, 246, 239),
    "wine": (143, 45, 42),
    "paper": (247, 242, 232),
    "slate": (39, 48, 47),
    "near-black": (21, 20, 19),
    "gold": (200, 169, 106),
    "wood": (148, 112, 75),
    "sand": (228, 213, 183),
    "sage": (156, 175, 136),
    "forest": (47, 107, 69),
    "sprout": (169, 196, 108),
    "navy": (31, 58, 95),
    "sky": (127, 178, 229),
    "terracotta": (196, 98, 63),
    "mustard": (209, 169, 58),
    "plum": (110, 59, 90),
    "rose": (217, 142, 155),
    "teal": (47, 127, 122),
    "charcoal": (51, 51, 51),
    "grey": (140, 140, 140),
    "white": (255, 255, 255),
    "black": (0, 0, 0),
}


# Colours


def parse(value: Any, behind: RGB | None = None) -> RGB | None:
    """`rgb(…)` or `rgba(…)` as computed by the browser, blended over `behind` when translucent."""
    match = re.fullmatch(
        r"rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)",
        str(value or "").strip(),
    )
    if match is None:
        return None
    rgb = [round(float(part)) for part in match.groups()[:3]]
    alpha_text = match.group(4)
    alpha = 1.0
    if alpha_text:
        alpha = float(alpha_text.rstrip("%")) / (100 if alpha_text.endswith("%") else 1)
    if alpha <= 0:
        return None
    if alpha < 1:
        under = behind or (255, 255, 255)
        rgb = [round(c * alpha + u * (1 - alpha)) for c, u in zip(rgb, under, strict=True)]
    return (rgb[0], rgb[1], rgb[2])


def hex_of(rgb: RGB) -> str:
    return "#" + "".join(f"{max(0, min(255, round(v))):02x}" for v in rgb)


def rgb_of(hex_: str) -> RGB:
    return (int(hex_[1:3], 16), int(hex_[3:5], 16), int(hex_[5:7], 16))


def luminance(rgb: RGB) -> float:
    def channel(value: int) -> float:
        v = value / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: RGB, b: RGB) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def readable(colour: RGB, bg: RGB, minimum: float) -> RGB:
    """The mockup's guard: 8 % toward black on a light background (white on a dark one) per
    step, at most 40 steps, until `minimum` contrast."""
    towards = 0 if luminance(bg) > 0.4 else 255
    c = colour
    for _ in range(MAX_STEPS):
        if contrast(c, bg) >= minimum:
            break
        c = mix(c, (towards,) * 3, STEP)
    return c


def shades(colour: RGB) -> tuple[RGB, RGB]:
    """`colour` and the darker shade the management site also sets text on."""
    return colour, mix(colour, (0, 0, 0), SHADE)


def reads_on(text: RGB, accents: Iterable[RGB]) -> bool:
    """Whether `text` reads at 4.5:1 on each accent and on its darker shade (#209)."""
    return all(contrast(text, shade) >= TEXT_CONTRAST for a in accents for shade in shades(a))


def titled(accent: RGB, bg: RGB, surface: RGB) -> RGB:
    """The main accent as the management site draws its small titles: 8 % a step away from the
    page (lighter on a dark one) until it reads at 4.5:1 on the page and the cards, else black or
    white, whichever reads better. The same steps as the web's `readable` (look.ts, #153), so the
    site draws the stored accent as it is."""
    page = (bg, surface)
    towards = 255 if luminance(bg) < DARK else 0
    c = accent
    for _ in range(MAX_STEPS):
        if all(contrast(c, b) >= TEXT_CONTRAST for b in page):
            return c
        c = mix(c, (towards,) * 3, STEP)
    black, white = (0, 0, 0), (255, 255, 255)
    return max((white, black), key=lambda t: min(contrast(t, b) for b in page))


def under(accent: RGB, text: RGB, margin: Callable[[RGB], float]) -> RGB:
    """`accent` moved away from `text`, 8 % a step, until `text` reads on it and its shade; then
    on in that direction while that brings it closer to holding on the page (`margin` up to 1)."""
    towards = 0 if luminance(text) > luminance(accent) else 255

    def step(c: RGB) -> RGB:
        return mix(c, (towards,) * 3, STEP)

    c = accent
    for _ in range(MAX_STEPS):
        if reads_on(text, [c]):
            break
        c = step(c)
    for _ in range(MAX_STEPS):
        after = step(c)
        if margin(c) >= 1 or margin(after) <= margin(c) or after == c:
            break
        c = after
    return c


def holds(role: str, colour: RGB, bg: RGB, surface: RGB) -> float:
    """How far `colour` clears what its role needs on the page (1 or more: it does): the main
    accent is also the small titles' colour, at 4.5:1 on the page and the cards; the second
    accent 3:1 on the page."""
    if role == "accent":
        return min(contrast(colour, b) for b in (bg, surface)) / TEXT_CONTRAST
    return contrast(colour, bg) / ACCENT_CONTRAST


def fit_text(
    accents: Mapping[str, RGB],
    bg: RGB,
    surface: RGB,
    preferred: RGB | None,
    movable: Collection[str],
) -> tuple[RGB, dict[str, RGB]]:
    """The text for buttons on both accents, and the accents moved for it (#209).

    `preferred` (the site's own, or the merchant's current one) if it reads at 4.5:1 on each
    accent and its darker shade, else white or near-black, the better first. If none does, the
    first of them that the `movable` accents can be moved under, away from it, while they still
    hold on the page (`holds`); then the same with every accent movable, so that a colour the
    merchant just picked stays as picked when another one can give way. When nothing holds, the
    try that comes closest.
    """

    def worst(text: RGB) -> float:
        return min(contrast(text, s) for a in accents.values() for s in shades(a))

    def page_margin(role: str) -> Callable[[RGB], float]:
        return lambda colour: holds(role, colour, bg, surface)

    candidates = ([preferred] if preferred else []) + sorted(TEXT_ON, key=worst, reverse=True)
    for text in candidates:
        if reads_on(text, accents.values()):
            return text, {}
    closest: tuple[float, RGB, dict[str, RGB]] | None = None
    for allowed in (set(movable), set(accents)):
        for text in candidates:
            if not reads_on(text, (c for name, c in accents.items() if name not in allowed)):
                continue
            moved = {
                name: under(c, text, page_margin(name))
                for name, c in accents.items()
                if name in allowed and not reads_on(text, [c])
            }
            if not reads_on(text, moved.values()):
                continue
            margin = min((holds(n, c, bg, surface) for n, c in moved.items()), default=1.0)
            if margin >= 1:
                return text, moved
            if closest is None or margin > closest[0]:
                closest = (margin, text, moved)
    return (closest[1], closest[2]) if closest else (candidates[0], {})


def as_drawn(role: str, colour: RGB, bg: RGB, surface: RGB) -> RGB:
    """An accent as the management site can use it (#209): the main one as the small titles'
    colour (`titled`), the second one at 3:1 on the page."""
    if role == "accent":
        return titled(colour, bg, surface)
    return readable(colour, bg, ACCENT_CONTRAST)


# Why an accent ends as it does: as asked, too faint on the page, moved for the buttons' text,
# or (the other accent, stored before #209) moved so that the titles read.
AccentReason = Literal["asked", "faint", "text", "titles"]


@dataclass(frozen=True)
class AccentChange:
    """An accent the merchant asked for, or the other one, moved for it."""

    role: str
    before: str
    after: str
    # The colour the merchant asked for; None for the accent that gave way.
    asked: str | None
    why: AccentReason


@dataclass(frozen=True)
class AccentFit:
    changes: tuple[AccentChange, ...]
    # The buttons' text on both accents.
    on_accent: str


def adjust_accents(current: Look, wanted: Mapping[str, str]) -> AccentFit:
    """The accents the merchant asked for (`wanted`: role to `#rrggbb`), as they can be stored.

    Each one as drawn (`as_drawn`), and the buttons' text at 4.5:1 on both accents and their
    darker shades (#209). The text changes, or a colour moves away from it, only when it
    doesn't read: the colour just picked first, the other one when that alone can't do it.
    """
    bg, surface = rgb_of(current.bg), rgb_of(current.surface)
    on_page = {role: as_drawn(role, rgb_of(asked), bg, surface) for role, asked in wanted.items()}
    accents = {role: on_page.get(role, rgb_of(getattr(current, role))) for role in ACCENTS}
    # A main colour stored before #209 may not read as titles yet: as the site draws it.
    accents["accent"] = on_page.get("accent", titled(accents["accent"], bg, surface))
    on, moved = fit_text(accents, bg, surface, rgb_of(current.on_accent), movable=wanted)
    changes = []
    for role, asked in wanted.items():
        used = hex_of(moved.get(role, on_page[role]))
        why: AccentReason = (
            "asked" if used == asked else "faint" if hex_of(on_page[role]) != asked else "text"
        )
        changes.append(AccentChange(role, getattr(current, role), used, asked, why))
    for role in ACCENTS:
        after = hex_of(moved.get(role, accents[role]))
        if role in wanted or after == getattr(current, role):
            continue
        why = "text" if role in moved else "titles"
        changes.append(AccentChange(role, getattr(current, role), after, None, why))
    return AccentFit(tuple(changes), hex_of(on))


def mix(colour: RGB, other: RGB, share: float) -> RGB:
    """`colour` moved `share` of the way to `other`."""
    r, g, b = (round(c + (o - c) * share) for c, o in zip(colour, other, strict=True))
    return r, g, b


def spread(rgb: RGB) -> int:
    return max(rgb) - min(rgb)


def hue(rgb: RGB) -> float:
    r, g, b = (v / 255 for v in rgb)
    high, low = max(r, g, b), min(r, g, b)
    if high == low:
        return 0.0
    d = high - low
    if high == r:
        h = ((g - b) / d) % 6
    elif high == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return h * 60


def hue_gap(a: RGB, b: RGB) -> float:
    gap = abs(hue(a) - hue(b)) % 360
    return min(gap, 360 - gap)


def name_of(rgb: RGB) -> str:
    return min(
        NAMES, key=lambda name: sum((x - y) ** 2 for x, y in zip(NAMES[name], rgb, strict=True))
    )


# The rules


def _weights(counts: Mapping[str, float], behind: RGB) -> dict[RGB, float]:
    weights: dict[RGB, float] = {}
    for value, weight in counts.items():
        rgb = parse(value, behind)
        if rgb is not None:
            weights[rgb] = weights.get(rgb, 0) + float(weight)
    return weights


def _texts(sample: Mapping[str, Any], bg: RGB) -> list[tuple[RGB, RGB, float]]:
    """(text colour, the colour behind it, characters) for every text the page shows."""
    rows = []
    for key, chars in (sample.get("texts") or {}).items():
        colour_text, _, behind_text = str(key).partition("|")
        behind = parse(behind_text, bg) or bg
        colour = parse(colour_text, behind)
        if colour is not None:
            rows.append((colour, behind, float(chars)))
    return rows


def _most(weights: Iterable[tuple[RGB, float]]) -> RGB | None:
    ranked = sorted(weights, key=lambda item: -item[1])
    return ranked[0][0] if ranked else None


def _font(family: str, bundled: Mapping[str, str]) -> tuple[str, str | None]:
    """The allow-list's font for a computed `font-family`, and the family it replaces when that
    isn't bundled nor generic (for a flag). The first family the rules know decides: `serif`,
    Georgia… give serif; `sans-serif`, Helvetica… give sans."""
    families = [f.strip().strip("\"'").lower() for f in family.split(",") if f.strip()]
    first = families[0] if families else ""
    if first in bundled:
        return bundled[first], None
    generic = "sans"
    for name in families:
        if name in bundled:
            generic = bundled[name]
            break
        if SANS.search(name):
            break
        if SERIF.search(name):
            generic = "serif"
            break
    original = next((f.strip().strip("\"'") for f in family.split(",") if f.strip()), "")
    return generic, None if first in GENERIC or not first else original


@dataclass(frozen=True)
class _Sample:
    """The page's sample (`probes.LOOK`), its colours parsed over the page's background."""

    raw: Mapping[str, Any]
    bg: RGB
    ink: RGB
    page: float
    backgrounds: dict[RGB, float]
    borders: dict[RGB, float]
    texts: list[tuple[RGB, RGB, float]]
    heading: Mapping[str, Any]


def _parsed(sample: Mapping[str, Any] | None) -> _Sample | None:
    """None when the page's background or text colour can't be read."""
    bg = parse(sample.get("bg")) if sample else None
    ink = parse(sample.get("ink"), bg) if sample and bg else None
    if sample is None or bg is None or ink is None:
        return None
    return _Sample(
        raw=sample,
        bg=bg,
        ink=ink,
        page=float(sample.get("page_area") or 1),
        backgrounds=_weights(sample.get("backgrounds") or {}, bg),
        borders=_weights(sample.get("borders") or {}, bg),
        texts=_texts(sample, bg),
        heading=sample.get("heading") or {},
    )


def read_look(sample: Mapping[str, Any] | None) -> tuple[Look | None, list[BuildFlag]]:
    """The look and the flags about it. None and a flag when nothing can be read: no look means
    the default one (`DEFAULT_LOOK`, #150)."""
    parsed = _parsed(sample)
    if parsed is None:
        return None, [NO_LOOK]
    accent, accent_2 = _pick_accents(parsed)
    surface = _pick_surface(parsed)
    muted = _pick_muted(parsed, surface, taken={parsed.ink, accent, accent_2})
    own = {"accent": accent, "accent_2": accent_2, "ink": parsed.ink, "muted": muted}
    checked, on_accent, flags = _contrast_flags(
        own, parsed.bg, surface, own_on=_own_on_accent(parsed, (accent, accent_2))
    )
    display, body, font_flags = _font_flags(parsed)
    corners, radius, texture = _shape(parsed)
    look = Look(
        bg=hex_of(parsed.bg),
        surface=hex_of(surface),
        line=hex_of(_pick_line(parsed)),
        ink=hex_of(checked["ink"]),
        muted=hex_of(checked["muted"]),
        accent=hex_of(checked["accent"]),
        accent_2=hex_of(checked["accent_2"]),
        on_accent=hex_of(on_accent),
        display_font=display,
        body_font=body,
        corners=corners,
        radius_px=radius,
        texture=texture,
    )
    return look, [*flags, *font_flags]


def _pick_accents(sample: _Sample) -> tuple[RGB, RGB]:
    """accent: the restaurant's name when it has its own colour, else the most used vivid one;
    accent_2: the most used vivid colour of another hue, else accent 12 % darker."""
    bg, ink = sample.bg, sample.ink
    vivid: dict[RGB, float] = {}
    for rgb, share in sample.backgrounds.items():
        vivid[rgb] = vivid.get(rgb, 0) + share / sample.page * 1000
    for rgb, count in sample.borders.items():
        vivid[rgb] = vivid.get(rgb, 0) + count * 20
    for rgb, _, chars in sample.texts:
        vivid[rgb] = vivid.get(rgb, 0) + chars
    vivid = {rgb: w for rgb, w in vivid.items() if spread(rgb) >= VIVID and rgb != ink}
    name_colour = parse(sample.heading.get("color"), bg)
    if name_colour is not None and name_colour != ink and spread(name_colour) >= VIVID:
        accent = name_colour
    else:
        accent = _most(vivid.items()) or mix(ink, bg, 0.2)
    other = [(rgb, w) for rgb, w in vivid.items() if hue_gap(rgb, accent) >= OTHER_HUE]
    return accent, _most(other) or mix(accent, (0, 0, 0), 0.12)


def _pick_surface(sample: _Sample) -> RGB:
    """The neutral background closest to the page's, on its lighter side."""
    bg = sample.bg
    surfaces = [
        rgb
        for rgb, share in sample.backgrounds.items()
        if share / sample.page >= SURFACE_SHARE
        and spread(rgb) < VIVID
        and luminance(rgb) > luminance(bg)
        and rgb != bg
    ]
    if not surfaces:
        return mix(bg, (255, 255, 255), 0.3)
    return min(surfaces, key=lambda rgb: luminance(rgb) - luminance(bg))


def _pick_line(sample: _Sample) -> RGB:
    """The most used neutral border, else the background 10 % toward the text."""
    bg, ink = sample.bg, sample.ink
    lines = [
        (rgb, n)
        for rgb, n in sample.borders.items()
        if spread(rgb) < VIVID and rgb not in (bg, ink)
    ]
    return _most(lines) or mix(bg, ink, 0.1)


def _pick_muted(sample: _Sample, surface: RGB, *, taken: set[RGB]) -> RGB:
    """The most used other readable text on the page or its cards."""
    on_page = {sample.bg, surface}
    counts: dict[RGB, float] = {}
    for rgb, behind, chars in sample.texts:
        if behind in on_page and rgb not in taken and contrast(rgb, behind) >= ACCENT_CONTRAST:
            counts[rgb] = counts.get(rgb, 0) + chars
    return _most(counts.items()) or mix(sample.ink, sample.bg, 0.35)


def _own_on_accent(sample: _Sample, accents: tuple[RGB, RGB]) -> RGB | None:
    """The text the page sets on either accent, if any."""
    counts: dict[RGB, float] = {}
    for rgb, behind, chars in sample.texts:
        if behind in accents:
            counts[rgb] = counts.get(rgb, 0) + chars
    return _most(counts.items())


def _contrast_flags(
    own: Mapping[str, RGB], bg: RGB, surface: RGB, *, own_on: RGB | None
) -> tuple[dict[str, RGB], RGB, list[BuildFlag]]:
    """Each colour as the management site can use it, the buttons' text, and one flag per
    colour that changed, with its final value.

    The accents as drawn (`as_drawn`), text at 4.5:1; then the buttons' text on both accents,
    which may move them further (`fit_text`)."""
    accents = {role: as_drawn(role, own[role], bg, surface) for role in ACCENTS}
    texts = {role: readable(own[role], bg, TEXT_CONTRAST) for role in ("ink", "muted")}
    on_accent, moved = fit_text(accents, bg, surface, own_on, movable=accents)
    checked = {**accents, **texts, **moved}
    flags = []
    for role, before in own.items():
        after = checked[role]
        if after == before:
            continue
        what = "text" if role in texts else name_of(before)
        moved_for_text = role in moved and accents[role] == before
        where = "under your buttons' text" if moved_for_text else "on your background"
        flags.append(
            BuildFlag(
                title=f"Your {what} ({hex_of(before)}) is hard to read {where}.",
                detail=f"The management site uses {hex_of(after)} instead, which reads well.",
            )
        )
    return checked, on_accent, flags


def _font_flags(sample: _Sample) -> tuple[DisplayFont, BodyFont, list[BuildFlag]]:
    """The headings' and the text's fonts from the allow-list, and a flag for each family the
    management site can't host."""
    raw = sample.raw
    heading_family = str(sample.heading.get("font") or raw.get("body_font") or "")
    display, display_was = _font(heading_family, FONTS_BUNDLED)
    body, body_was = _font(str(raw.get("body_font") or ""), BODY_BUNDLED)
    flags = []
    for kind, was, used in (("titles use", display_was, display), ("text uses", body_was, body)):
        if was:
            closest = "serif" if used == "serif" else "sans-serif" if used == "sans" else used
            flags.append(
                BuildFlag(
                    title=f"Your {kind} {_font_name(was)}, which the management site can't host.",
                    detail=f"It uses the closest it has: a {closest}.",
                )
            )
    return _display(display), _body(body), flags


def _shape(sample: _Sample) -> tuple[Corners, int, Texture]:
    """The corners, the cards' radius and the texture."""
    raw = sample.raw
    radius = _radius(raw.get("radii") or [], float(raw.get("page_width") or 0))
    blobs = float(raw.get("blob_area") or 0) / sample.page
    # Drawn shapes first: an organic site's bands are square, its blobs aren't.
    corners: Corners = "organic" if blobs >= BLOB_SHARE else "square" if radius == 0 else "rounded"
    texture: Texture = "leather" if int(raw.get("textures") or 0) > 0 else "none"
    return corners, radius, texture


# A family is the page's own text, and flags reach the assistant's model (ARCHITECTURE.md §4):
# a flag only names a family of this list, in the list's spelling, never the page's (#197).
KNOWN_FONTS = (
    "Iowan Old Style", "Palatino", "Georgia", "Times New Roman", "Garamond", "EB Garamond",
    "Cormorant Garamond", "Libre Baskerville", "Baskerville", "Book Antiqua", "Playfair Display",
    "Merriweather", "Lora", "PT Serif", "Noto Serif", "DM Serif Display", "Crimson Text",
    "Montserrat", "Lato", "Roboto", "Open Sans", "Inter", "Poppins", "Raleway", "Nunito",
    "Oswald", "Source Sans Pro", "Source Sans 3", "Noto Sans", "DM Sans", "Work Sans",
    "Helvetica", "Helvetica Neue", "Arial", "Verdana", "Segoe UI", "Futura", "Gill Sans",
)  # fmt: skip
_KNOWN = {font.lower(): font for font in KNOWN_FONTS}


def _font_name(family: str) -> str:
    """The family as `KNOWN_FONTS` spells it, else "a font": never the page's own words."""
    return _KNOWN.get(" ".join(family.split()).lower(), "a font")


def _display(font: str) -> DisplayFont:
    return "Fraunces Soft" if font == "Fraunces Soft" else "serif" if font == "serif" else "sans"


def _body(font: str) -> BodyFont:
    return "Karla" if font == "Karla" else "serif" if font == "serif" else "sans"


def _radius(radii: Iterable[Any], page_width: float) -> int:
    """The most common corner radius of the cards (boxes over 80 by 40 that aren't pills), else
    of any box, capped at `MAX_RADIUS`. Full-width boxes are layout (bands, the header), not
    cards: they don't count."""
    cards: dict[int, int] = {}
    boxes: dict[int, int] = {}
    for item in radii:
        try:
            px, width, height = (float(v) for v in item)
        except (TypeError, ValueError):
            continue
        if page_width and width >= 0.9 * page_width:
            continue
        pill = px >= min(width, height) / 2
        value = min(MAX_RADIUS, round(px))
        boxes[value] = boxes.get(value, 0) + 1
        if width > 80 and height > 40 and not pill:
            cards[value] = cards.get(value, 0) + 1
    counts = cards or boxes
    if not counts:
        return 0
    # Ties go to the larger radius: a site with as many round as square boxes reads as round.
    return max(counts, key=lambda value: (counts[value], value))


# The review card's line (#136)

FONT_WORDS = {
    "Fraunces Soft": "Soft italic titles",
    "serif": "Serif titles",
    "sans": "Sans-serif titles",
}
CORNER_WORDS = {
    "organic": "Organic shapes",
    "rounded": "Rounded corners",
    "square": "Square corners",
}


def describe(look: Look) -> str:
    """ "Olive, brick and cream. Soft italic titles. Organic shapes." """
    names = list(
        dict.fromkeys(name_of(rgb_of(c)) for c in (look.accent, look.accent_2, look.surface))
    )
    return (
        f"{joined(names).capitalize()}. {FONT_WORDS[look.display_font]}. "
        f"{CORNER_WORDS[look.corners]}."
    )
