import { describe, expect, it } from "vitest";
import graineVerte from "../../../tests/fixtures/la-graine-verte.json" with { type: "json" };
import maisonAlbane from "../../../tests/fixtures/maison-albane.json" with { type: "json" };
import petitZinc from "../../../tests/fixtures/le-petit-zinc.json" with { type: "json" };
import type { Look } from "../types.ts";
import { DEFAULT_LOOK, contrast, cornersOf, lookStyle, readable } from "./look.ts";

const zinc = petitZinc.look as Look;
const albane = maisonAlbane.look as Look;
const variables = (look: Look) => lookStyle(look) as Record<string, string>;

describe("lookStyle", () => {
  it("leaves La Graine Verte, and a merchant without a look, on tokens.css", () => {
    expect(graineVerte.look).toEqual(DEFAULT_LOOK);
    expect(lookStyle(graineVerte.look as Look)).toEqual({});
    expect(lookStyle(null)).toEqual({});
    expect(cornersOf(null)).toBe("organic");
  });

  it("maps a look to the tokens, with the darker shades derived", () => {
    expect(variables(zinc)).toMatchObject({
      "--bg": "#f7f2e8",
      "--cream": "#fffaf0",
      "--line": "#ddcfb8",
      "--ink": "#2a2320",
      "--mut": "#675c54",
      "--olive": "#8f2d2a",
      "--brick": "#7e2825",
      "--on": "#eeddd7",
      "--olive-band": "#842927",
      "--brick-deep": "#742522",
      "--line-2": "#c7baa6",
      "--display": 'Georgia, "Times New Roman", serif',
      "--body": '"Helvetica Neue", Arial, sans-serif',
      "--red": "#a3322a",
      "--radius": "10px",
      "--leather": "none",
      colorScheme: "light",
    });
    expect(variables(zinc)["--stroke-brick"]).toContain("fill='%237e2825'");
  });

  it("gives square corners no radius, and a dark page a dark colour scheme", () => {
    expect(variables(albane)).toMatchObject({ "--radius": "0px", colorScheme: "dark" });
    expect(cornersOf(albane)).toBe("square");
  });

  // No text under 4.5:1, for each demo merchant: tests/look-contrast.test.ts.

  it("darkens an accent too light for text, and the text on it follows", () => {
    // A merchant's mid green under a near-white: 2.9:1 as a title, 3.1:1 under its text.
    const v = variables({ ...zinc, accent: "#5f9f5a", on_accent: "#f4f2ea" });
    expect(v["--olive"]).not.toBe("#5f9f5a");
    for (const background of [v["--bg"], v["--cream"]]) expect(contrast(v["--olive"], background)).toBeGreaterThanOrEqual(4.5);
    for (const fill of [v["--olive"], v["--olive-band"]]) expect(contrast(v["--on"], fill)).toBeGreaterThanOrEqual(4.5);
  });
});

describe("the accent and the text on it", () => {
  // Titles in the accent on the page and the cards, and text on the accent and its band.
  const pairs = (v: Record<string, string>) => [
    contrast(v["--olive"], v["--bg"]),
    contrast(v["--olive"], v["--cream"]),
    contrast(v["--on"], v["--olive"]),
    contrast(v["--on"], v["--olive-band"]),
  ];

  it("lightens an accent too dark for a dark page, and the text on it follows", () => {
    const v = variables({ ...albane, bg: "#121212", surface: "#1c1c1c", accent: "#2a3a5a", on_accent: "#ffffff" });
    expect(v["--olive"]).not.toBe("#2a3a5a");
    for (const ratio of pairs(v)) expect(ratio).toBeGreaterThanOrEqual(4.5);
  });

  it("gives the text black or white when its own colour never reads", () => {
    const v = variables({ ...zinc, bg: "#ffffff", surface: "#ffffff", accent: "#0066ff", on_accent: "#000000" });
    for (const ratio of pairs(v)) expect(ratio).toBeGreaterThanOrEqual(4.5);
  });

  it("moves the accent on when no text colour reads on it and its band", () => {
    // A blue lightened just enough to read on black: neither black nor white reads on it and on
    // its band at once (4.37:1 at best), so it lightens further.
    const v = variables({ ...albane, bg: "#000000", surface: "#000000", accent: "#0000ff", on_accent: "#000000" });
    for (const ratio of pairs(v)) expect(ratio).toBeGreaterThanOrEqual(4.5);
  });

  it("holds 4.5:1 across pages, accents and text colours", () => {
    const pages: [string, string][] = [
      ["#ffffff", "#ffffff"], ["#f7f2e8", "#fffaf0"], ["#edebe2", "#f8f6ef"],
      ["#3a3a3a", "#444444"], ["#1e1b18", "#26221e"], ["#121212", "#1c1c1c"], ["#000000", "#000000"],
    ];
    const accents = [
      "#727f55", "#8f2d2a", "#c8a96a", "#0066ff", "#009999", "#4f9a4a", "#1f3a5f", "#ff8800",
      "#888888", "#e0e0e0", "#202020", "#000000", "#000099", "#0000ff", "#001100",
    ];
    const texts = ["#ffffff", "#000000", "#f4f2ea", "#111111", "#777777"];
    for (const [bg, surface] of pages) {
      for (const accentColour of accents) {
        for (const on_accent of texts) {
          const v = variables({ ...zinc, bg, surface, accent: accentColour, on_accent });
          const worst = Math.min(...pairs(v));
          expect(worst, `${bg} ${accentColour} ${on_accent}`).toBeGreaterThanOrEqual(4.5);
        }
      }
    }
  });
});

describe("readable", () => {
  it("keeps the error red where it reads, and lightens it on a dark page", () => {
    expect(readable("#a3322a", ["#f7f2e8"])).toBe("#a3322a");
    const onDark = readable("#a3322a", [albane.bg, albane.surface]);
    expect(onDark).not.toBe("#a3322a");
    expect(contrast(onDark, albane.surface)).toBeGreaterThanOrEqual(4.5);
  });

  it("falls back to black or white when moving the colour never gets there", () => {
    // Two backgrounds pulling apart: no shade of grey reads on both, white reads best.
    expect(readable("#808080", ["#2a2a2a", "#767676"])).toBe("#ffffff");
  });
});
