// Text on the management site reads at 4.5:1 for each demo merchant (#153): La Graine Verte
// through tokens.css, the others through `lookStyle`. On Node's runner, which reads the file.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import maisonAlbane from "../../tests/fixtures/maison-albane.json" with { type: "json" };
import petitZinc from "../../tests/fixtures/le-petit-zinc.json" with { type: "json" };
import { DEFAULT_LOOK, contrast, lookStyle } from "../src/management/look.ts";
import type { Look } from "../src/types.ts";

const css = readFileSync(new URL("../src/management/tokens.css", import.meta.url), "utf8");
const tokens = Object.fromEntries([...css.matchAll(/(--[\w-]+): (#[0-9a-f]{6});/g)].map(([, name, value]) => [name, value]));
const variables = (look: Look) => lookStyle(look) as Record<string, string>;

const MERCHANTS: [string, Record<string, string>][] = [
  ["La Graine Verte", tokens],
  ["Le Petit Zinc", variables(petitZinc.look as Look)],
  ["Maison Albane", variables(maisonAlbane.look as Look)],
];

test("tokens.css holds the olive lookStyle would give La Graine Verte", () => {
  // The default look sets no variable; one pixel of radius more and lookStyle computes them all.
  const computed = variables({ ...DEFAULT_LOOK, radius_px: DEFAULT_LOOK.radius_px + 1 });
  assert.equal(tokens["--olive"], computed["--olive"]);
  assert.equal(tokens["--olive-band"], computed["--olive-band"]);
});

for (const [name, v] of MERCHANTS) {
  test(`${name} has no text under 4.5:1`, () => {
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
      assert.ok(foreground && background, `${pair}: missing variable`);
      assert.ok(contrast(foreground, background) >= 4.5, `${pair}: ${contrast(foreground, background).toFixed(2)}:1`);
    }
  });
}
