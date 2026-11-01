import type { CSSProperties } from "react";
import type { Look } from "../types.ts";

// The management site in the merchant's own look (#150): `Look` (#149) mapped to the tokens of
// `tokens.css`, set on the site's root.

/**
 * La Graine Verte's look. `tokens.css` holds its values as the mockup picked them by hand (its
 * darker shades follow no single formula), so this look sets no variable and the site stays the
 * mockup, but for the olive, darker there as `lookStyle` would make it. A merchant without a look
 * gets it too.
 */
export const DEFAULT_LOOK: Look = {
  bg: "#edebe2",
  surface: "#f8f6ef",
  line: "#d5d2c3",
  ink: "#47522f",
  muted: "#5f6a45",
  accent: "#727f55",
  accent_2: "#935046",
  on_accent: "#f4f2ea",
  display_font: "Fraunces Soft",
  body_font: "Karla",
  corners: "organic",
  radius_px: 30,
  texture: "leather",
};

// Errors don't follow the brand, but must read on any background (4.5:1, as #149's `readable`).
const RED = "#a3322a";
const TEXT_CONTRAST = 4.5;
const STEP = 0.08;
const MAX_STEPS = 40;
// The darker shades of the accents: the band under the sidebar and cards, the deep brick.
const SHADE = 0.08;
const ORGANIC_RADIUS = 30;

const SERIF = 'Georgia, "Times New Roman", serif';
const SANS = '"Helvetica Neue", Arial, sans-serif';
const DISPLAY: Record<Look["display_font"], string> = {
  "Fraunces Soft": `"Fraunces Soft", ${SERIF}`,
  serif: SERIF,
  sans: SANS,
};
const BODY: Record<Look["body_font"], string> = { Karla: `"Karla", ${SANS}`, serif: SERIF, sans: SANS };

export type Corners = Look["corners"];

/** The CSS variables of `look` for the site's root; none for the default look. */
export function lookStyle(look: Look | null | undefined): CSSProperties {
  if (!look || sameLook(look, DEFAULT_LOOK)) return {};
  const { olive, band, on } = accent(look);
  const variables: Record<string, string> = {
    "--bg": look.bg,
    "--cream": look.surface,
    "--line": look.line,
    "--ink": look.ink,
    "--mut": look.muted,
    "--olive": olive,
    "--brick": look.accent_2,
    "--on": on,
    "--olive-band": band,
    "--brick-deep": darker(look.accent_2, SHADE),
    "--line-2": darker(look.line, 0.1),
    "--display": DISPLAY[look.display_font],
    "--body": BODY[look.body_font],
    "--red": readable(RED, [look.bg, look.surface]),
    "--radius": `${radiusOf(look)}px`,
    "--stroke-brick": stroke(look.accent_2),
  };
  if (look.texture === "none") variables["--leather"] = "none";
  return { ...variables, colorScheme: isDark(look.bg) ? "dark" : "light" } as CSSProperties;
}

/** `organic` draws blobs, strokes and doodles; `rounded` and `square` draw plain boxes. */
export function cornersOf(look: Look | null | undefined): Corners {
  return look?.corners ?? "organic";
}

function radiusOf(look: Look): number {
  if (look.corners === "organic") return ORGANIC_RADIUS;
  return look.corners === "rounded" ? look.radius_px : 0;
}

function sameLook(a: Look, b: Look): boolean {
  return (Object.keys(b) as (keyof Look)[]).every((key) => a[key] === b[key]);
}

/** The mockup's hand-drawn underline (`tokens.css`), in `color`. */
function stroke(color: string): string {
  const fill = `%23${color.slice(1)}`;
  return `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 300 36' preserveAspectRatio='none'%3E%3Cpath fill='${fill}' d='M2 22C70 11 170 5 297 6c3 0 3 3 0 3C200 11 120 16 42 26 20 29 0 27 2 22Z'/%3E%3Cpath fill='${fill}' d='M92 29c38-7 76-11 112-14 3 0 3 3 0 4-34 4-72 10-110 15-4 0-6-3-2-5Z'/%3E%3C/svg%3E")`;
}

/**
 * The accent, its band and the text on them, at 4.5:1 whatever the merchant picked (#153): the
 * accent moves away from the page (darker, or lighter on a dark page) until titles in it read on
 * the page and the cards, then further if no text colour reads on both it and its band. The
 * second accent is the platform's to check (#209).
 */
function accent(look: Look): { olive: string; band: string; on: string } {
  const away = isDark(look.bg) ? lighter : darker;
  let olive = readable(look.accent, [look.bg, look.surface]);
  for (let step = 0; ; step++) {
    const band = darker(olive, SHADE);
    const on = readable(look.on_accent, [olive, band]);
    if (step === MAX_STEPS || reads(on, [olive, band])) return { olive, band, on };
    olive = away(olive, STEP);
  }
}

/**
 * `color` as is if it reads on every background, else lightened (dark background) or darkened,
 * and black or white, whichever reads better, if that never gets there.
 */
export function readable(color: string, backgrounds: readonly string[]): string {
  const lighten = isDark(backgrounds[0]);
  let current = color;
  for (let step = 0; step < MAX_STEPS; step++) {
    if (reads(current, backgrounds)) return current;
    current = lighten ? lighter(current, STEP) : darker(current, STEP);
  }
  const worst = (candidate: string) => Math.min(...backgrounds.map((background) => contrast(candidate, background)));
  return worst("#000000") > worst("#ffffff") ? "#000000" : "#ffffff";
}

function reads(color: string, backgrounds: readonly string[]): boolean {
  return backgrounds.every((background) => contrast(color, background) >= TEXT_CONTRAST);
}

export function contrast(a: string, b: string): number {
  const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (high + 0.05) / (low + 0.05);
}

function darker(color: string, by: number): string {
  return hex(channels(color).map((c) => c * (1 - by)));
}

function lighter(color: string, by: number): string {
  return hex(channels(color).map((c) => c + (255 - c) * by));
}

function isDark(color: string): boolean {
  return luminance(color) < 0.18;
}

function luminance(color: string): number {
  const [r, g, b] = channels(color).map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function channels(color: string): number[] {
  return [1, 3, 5].map((i) => Number.parseInt(color.slice(i, i + 2), 16));
}

function hex(values: number[]): string {
  return `#${values
    .map((v) =>
      Math.round(Math.min(255, Math.max(0, v)))
        .toString(16)
        .padStart(2, "0"),
    )
    .join("")}`;
}
