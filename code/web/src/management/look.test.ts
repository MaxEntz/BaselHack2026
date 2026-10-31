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

  it.each([
    ["Le Petit Zinc", zinc],
    ["Maison Albane", albane],
  ])("%s has no text under 4.5:1", (_, look) => {
    const v = variables(look);
    const text: [string, string, string][] = [
      ["ink", v["--ink"], v["--bg"]],
      ["ink on cards", v["--ink"], v["--cream"]],
      ["muted", v["--mut"], v["--bg"]],
      ["muted on cards", v["--mut"], v["--cream"]],
      ["titles", v["--olive"], v["--bg"]],
      ["titles on cards", v["--olive"], v["--cream"]],
      ["on the accent", v["--on"], v["--olive"]],
      ["on the second accent", v["--on"], v["--brick"]],
      ["on the band", v["--on"], v["--olive-band"]],
      ["on the deep brick", v["--on"], v["--brick-deep"]],
      ["errors", v["--red"], v["--bg"]],
      ["errors on cards", v["--red"], v["--cream"]],
    ];
    for (const [pair, foreground, background] of text) {
      expect(contrast(foreground, background), pair).toBeGreaterThanOrEqual(4.5);
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
});
