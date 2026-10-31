"""A merchant's `Look`, from its homepage's computed styles (#149).

Pure code, no LLM (ADR-0003): the crawler samples the page (`crawl._LOOK`), and these rules
give each colour a role. The contrast guard is the mockup's `readable()`
(documentation/design/poc-v10/sesame-studio.html). The colours' names and the flags are for the
review card (#136).
"""

import re
from collections.abc import Iterable, Mapping
from typing import Any

from core.models import BodyFont, BuildFlag, DisplayFont, Look

RGB = tuple[int, int, int]

# #150: no look means La Graine Verte's, the management mockup's own tokens.
DEFAULT_LOOK = Look(
    bg="#edebe2",
    surface="#f8f6ef",
    line="#d5d2c3",
    ink="#47522f",
    muted="#5f6a45",
    accent="#727f55",
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
MAX_RADIUS = 32

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
    for _ in range(40):
        if contrast(c, bg) >= minimum:
            break
        c = tuple(round(v + (towards - v) * 0.08) for v in c)  # type: ignore[assignment]
    return c


def mix(colour: RGB, other: RGB, share: float) -> RGB:
    """`colour` moved `share` of the way to `other`."""
    return tuple(round(c + (o - c) * share) for c, o in zip(colour, other, strict=True))  # type: ignore[return-value]


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


def read_look(sample: Mapping[str, Any] | None) -> tuple[Look | None, list[BuildFlag]]:
    """The look and the flags about it. None and a flag when nothing can be read: no look means
    the default one (`DEFAULT_LOOK`, #150)."""
    bg = parse(sample.get("bg")) if sample else None
    ink_read = parse(sample.get("ink"), bg) if sample and bg else None
    if sample is None or bg is None or ink_read is None:
        return None, [NO_LOOK]
    page = float(sample.get("page_area") or 1)
    backgrounds = _weights(sample.get("backgrounds") or {}, bg)
    borders = _weights(sample.get("borders") or {}, bg)
    texts = _texts(sample, bg)
    heading = sample.get("heading") or {}

    # accent: the restaurant's name when it has its own colour, else the most used vivid one.
    name_colour = parse(heading.get("color"), bg)
    vivid: dict[RGB, float] = {}
    for rgb, share in backgrounds.items():
        vivid[rgb] = vivid.get(rgb, 0) + share / page * 1000
    for rgb, count in borders.items():
        vivid[rgb] = vivid.get(rgb, 0) + count * 20
    for rgb, _, chars in texts:
        vivid[rgb] = vivid.get(rgb, 0) + chars
    vivid = {rgb: w for rgb, w in vivid.items() if spread(rgb) >= VIVID and rgb != ink_read}
    if name_colour is not None and name_colour != ink_read and spread(name_colour) >= VIVID:
        accent = name_colour
    else:
        accent = _most(vivid.items()) or mix(ink_read, bg, 0.2)
    # accent_2: the most used vivid colour of another hue, else accent 12 % darker.
    other = [(rgb, w) for rgb, w in vivid.items() if hue_gap(rgb, accent) >= OTHER_HUE]
    accent_2 = _most(other) or mix(accent, (0, 0, 0), 0.12)

    # surface: the neutral background closest to the page's, on its lighter side.
    surfaces = [
        rgb
        for rgb, share in backgrounds.items()
        if share / page >= SURFACE_SHARE
        and spread(rgb) < VIVID
        and luminance(rgb) > luminance(bg)
        and rgb != bg
    ]
    surface = (
        min(surfaces, key=lambda rgb: luminance(rgb) - luminance(bg))
        if surfaces
        else mix(bg, (255, 255, 255), 0.3)
    )
    # line: the most used neutral border, else the background 10 % toward the text.
    lines = [
        (rgb, n) for rgb, n in borders.items() if spread(rgb) < VIVID and rgb not in (bg, ink_read)
    ]
    line = _most(lines) or mix(bg, ink_read, 0.1)
    # muted: the most used other readable text on the page or its cards.
    on_page = {bg, surface}
    taken = {ink_read, accent, accent_2}
    muted_counts: dict[RGB, float] = {}
    for rgb, behind, chars in texts:
        if behind in on_page and rgb not in taken and contrast(rgb, behind) >= ACCENT_CONTRAST:
            muted_counts[rgb] = muted_counts.get(rgb, 0) + chars
    muted = _most(muted_counts.items()) or mix(ink_read, bg, 0.35)
    # on_accent: the text set on either accent, else white or near-black, whichever reads.
    on_counts: dict[RGB, float] = {}
    for rgb, behind, chars in texts:
        if behind in (accent, accent_2):
            on_counts[rgb] = on_counts.get(rgb, 0) + chars
    on_accent = _most(on_counts.items())
    if on_accent is None or contrast(on_accent, accent) < ACCENT_CONTRAST:
        white, dark = (255, 255, 255), (17, 17, 17)
        on_accent = white if contrast(white, accent) >= contrast(dark, accent) else dark

    flags: list[BuildFlag] = []
    adjusted: list[tuple[str, RGB, RGB]] = []
    checked = {}
    for role, colour, minimum in (
        ("accent", accent, ACCENT_CONTRAST),
        ("accent_2", accent_2, ACCENT_CONTRAST),
        ("ink", ink_read, TEXT_CONTRAST),
        ("muted", muted, TEXT_CONTRAST),
    ):
        fixed = readable(colour, bg, minimum)
        checked[role] = fixed
        if fixed != colour:
            adjusted.append((role, colour, fixed))
    for role, before, after in adjusted:
        what = "text" if role in ("ink", "muted") else name_of(before)
        flags.append(
            BuildFlag(
                title=f"Your {what} ({hex_of(before)}) is hard to read on your background.",
                detail=f"The management site uses {hex_of(after)} instead, which reads well.",
            )
        )

    display, display_was = _font(
        str(heading.get("font") or sample.get("body_font") or ""), FONTS_BUNDLED
    )
    body, body_was = _font(str(sample.get("body_font") or ""), BODY_BUNDLED)
    for kind, was, used in (("titles use", display_was, display), ("text uses", body_was, body)):
        if was:
            closest = "serif" if used == "serif" else "sans-serif" if used == "sans" else used
            flags.append(
                BuildFlag(
                    title=f"Your {kind} {_font_name(was)}, which the management site can't host.",
                    detail=f"It uses the closest it has: a {closest}.",
                )
            )

    radius = _radius(sample.get("radii") or [], float(sample.get("page_width") or 0))
    blobs = float(sample.get("blob_area") or 0) / page
    # Drawn shapes first: an organic site's bands are square, its blobs aren't.
    corners = "organic" if blobs >= BLOB_SHARE else "square" if radius == 0 else "rounded"
    texture = "leather" if int(sample.get("textures") or 0) > 0 else "none"

    look = Look(
        bg=hex_of(bg),
        surface=hex_of(surface),
        line=hex_of(line),
        ink=hex_of(checked["ink"]),
        muted=hex_of(checked["muted"]),
        accent=hex_of(checked["accent"]),
        accent_2=hex_of(checked["accent_2"]),
        on_accent=hex_of(on_accent),
        display_font=_display(display),
        body_font=_body(body),
        corners=corners,
        radius_px=radius,
        texture=texture,
    )
    return look, flags


# A family is the page's own text, and flags reach the assistant's model (ARCHITECTURE.md §4):
# only a name that looks like a font's is repeated, never a sentence (#197 review).
FONT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .'-]{0,39}$")


def _font_name(family: str) -> str:
    """`family` if it looks like a font name (40 characters, 4 words at most), else "a font"."""
    if FONT_NAME.fullmatch(family) and len(family.split()) <= 4:
        return family
    return "a font"


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
    colours = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
    return f"{colours.capitalize()}. {FONT_WORDS[look.display_font]}. {CORNER_WORDS[look.corners]}."
