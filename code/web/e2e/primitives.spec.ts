import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { type Page, expect, test } from "@playwright/test";
import { openMockup } from "./mockup.ts";

// Each primitive in /?gallery against the same markup inside the v10 mockup, at 1440 px:
// the computed styles that make up its look must match. SESAME_MOCKUP points to another copy;
// without a mockup, this file is skipped.
const MOCKUP = process.env.SESAME_MOCKUP ?? resolve(import.meta.dirname, "../../../documentation/design/poc-v10/sesame-management.html");

const PROPS = [
  "min-height", "padding-top", "padding-right", "padding-bottom", "padding-left",
  "border-top-width", "border-top-style", "border-top-color", "border-top-left-radius",
  "font-family", "font-size", "font-weight", "font-style", "line-height", "letter-spacing",
  "color", "background-color", "box-shadow", "width", "height", "stroke-width", "accent-color",
] as const;

// [name, how to reach ours in /?gallery, the mockup's markup (in the same kind of parent as in the
// gallery, since sizes are inherited; `data-probe` marks the element if it isn't the outer one),
// where to put it in the mockup]
const CASES: [string, (page: Page) => Promise<string>, string, string][] = [
  ["Pill", async () => '[data-primitive="pill"]', '<button class="pill">Default</button>', "main"],
  ["Pill primary", async () => '[data-primitive="pill-pri"]', '<button class="pill pri">Primary</button>', "main"],
  ["Pill small", async () => '[data-primitive="pill-sm"]', '<button class="pill sm">Small</button>', "main"],
  ["Tag", async () => '[data-primitive="tag"]', '<span class="tag">vegan</span>', "main"],
  ["Box olive", async () => '[data-primitive="box-olive"]', '<section class="box olive on-dark">x</section>', "main"],
  ["Box brick", async () => '[data-primitive="box-brick"]', '<section class="box brick on-dark">x</section>', "main"],
  ["Box cream", async () => '[data-primitive="box-cream"]', '<section class="box cream">x</section>', "main"],
  ["Field", async () => '[data-primitive="field"]', '<input type="text" value="La Graine Verte">', "main"],
  ["Checkbox", async () => '[data-primitive="checkbox"]', '<div class="checks"><label><input type="checkbox" checked data-probe>Vegan</label></div>', "main"],
  ["Icon", async () => '[data-primitive="icon-bell"] svg', '<span style="font-size: 28px"><svg class="ico" data-probe><use href="#i-bell"/></svg></span>', "main"],
  [
    "Toast",
    async (page) => {
      await page.getByRole("button", { name: "Toast", exact: true }).click();
      return ".toast";
    },
    '<div class="toast">20:00 is blocked.</div>',
    "#toasts",
  ],
  [
    "SaveBar",
    async (page) => {
      await page.getByLabel("Largest group").last().fill("6");
      return ".savebar";
    },
    '<div class="savebar"><span>Unsaved changes</span></div>',
    "main",
  ],
  [
    "Dialog",
    async (page) => {
      await page.getByRole("button", { name: "Open a dialog" }).click();
      return "dialog";
    },
    "<dialog open><p>x</p></dialog>",
    "body",
  ],
];

async function styles(page: Page, selector: string) {
  return page.locator(selector).first().evaluate((element, props) => {
    const style = getComputedStyle(element);
    return Object.fromEntries(props.map((prop) => [prop, style.getPropertyValue(prop)]));
  }, PROPS as unknown as string[]);
}

test.describe("primitives match the mockup", () => {
  test.skip(!existsSync(MOCKUP), `mockup not found: ${MOCKUP}`);

  for (const [name, ours, markup, parent] of CASES) {
    test(name, async ({ page, browser }) => {
      await page.goto("/?gallery");
      await page.evaluate(() => document.fonts.ready);
      const mine = await styles(page, await ours(page));

      const mockup = await browser.newPage({ viewport: { width: 1440, height: 900 } });
      await openMockup(mockup, pathToFileURL(MOCKUP).href);
      await mockup.evaluate(
        ([html, where]) => {
          const holder = document.createElement("div");
          holder.innerHTML = html;
          const element = holder.firstElementChild!;
          if (!element.querySelector("[data-probe]")) element.setAttribute("data-probe", "");
          document.querySelector(where)!.append(element);
        },
        [markup, parent],
      );
      const theirs = await styles(mockup, "[data-probe]");
      await mockup.close();

      // Widths of content-sized elements depend on their text; compare the look, not the text.
      for (const prop of ["width", "height"] as const) {
        if (!["Icon", "Checkbox"].includes(name)) {
          delete (mine as Record<string, string>)[prop];
          delete (theirs as Record<string, string>)[prop];
        }
      }
      expect(mine).toEqual(theirs);
    });
  }
});
