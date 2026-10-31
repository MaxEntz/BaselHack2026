import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { type Page, expect, test } from "@playwright/test";

// The management shell (#137) beside the v10 mockup, at 1440 and 390 px, with the demo fixture
// and its clock at 19:40: the sidebar, the top bar and the assistant's column take the same
// place, with the same look and text. The page under them is each page's own issue.
const MOCKUP = process.env.SESAME_MOCKUP ?? resolve(import.meta.dirname, "../../../documentation/design/poc-v10/sesame-management.html");
const DEMO_NOW = new Date("2026-10-31T19:40:00+01:00");

const LOOK = [
  "font-family", "font-size", "font-weight", "font-style", "line-height", "color", "background-color",
  "padding-top", "padding-right", "padding-bottom", "padding-left", "border-top-left-radius", "display",
] as const;

// [what, selector in both pages, compare its text]. Containers hold the pages' own content,
// so only their place and look are compared.
const PARTS: [string, string, boolean][] = [
  ["sidebar", ".side", false],
  ["brand", ".brand", true],
  ["brand line", ".brand-sub", true],
  ["main nav", '.side nav[aria-label="Main"]', true],
  ["current link", '.side a[aria-current="page"]', true],
  ["restaurant heading", ".side h3", true],
  ["settings", '.side nav[aria-label="Account"] a', true],
  ["top bar", ".top", false],
  ["search", ".search input", false],
  ["clock", ".clock", true],
  ["bell", ".bell", false],
  // The owner's initials ("C"): the mockup made up a surname ("CL").
  ["avatar", ".avatar", false],
  ["assistant", ".asst", false],
  ["assistant title", ".asst h2", true],
];

// Subpixel layout: a box may round one pixel apart.
function near(mine: Seen["box"], theirs: Seen["box"]): boolean {
  if (!mine || !theirs) return mine === theirs;
  return (["x", "y", "width", "height"] as const).every((key) => Math.abs(mine[key] - theirs[key]) <= 1);
}

type Seen = { box: { x: number; y: number; width: number; height: number } | null; text: string; look: Record<string, string> };

async function seen(page: Page, selector: string): Promise<Seen | null> {
  const element = page.locator(selector).first();
  if ((await element.count()) === 0) return null;
  return element.evaluate((node, props) => {
    const style = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    const visible = style.display !== "none" && rect.width > 0;
    return {
      box: visible ? { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) } : null,
      text: (node as HTMLElement).innerText.trim(),
      look: visible ? Object.fromEntries(props.map((prop) => [prop, style.getPropertyValue(prop)])) : {},
    };
  }, LOOK as unknown as string[]);
}

async function settle(page: Page) {
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))));
}

for (const width of [1440, 390]) {
  test.describe(`shell at ${width} px`, () => {
    test.skip(!existsSync(MOCKUP), `mockup not found: ${MOCKUP}`);
    test.use({ viewport: { width, height: 900 } });

    test("matches the mockup", async ({ page, browser }) => {
      await page.clock.install({ time: DEMO_NOW });
      await page.goto("/manage/la-graine-verte");
      await expect(page.locator(".brand-sub")).toHaveText("Mulhouse, €");
      if (width > 820) await expect(page.locator(".clock")).toHaveText("Sat 31 Oct, 19:40");
      await settle(page);

      const mockup = await browser.newPage({ viewport: { width, height: 900 } });
      await mockup.goto(`${pathToFileURL(MOCKUP).href}#home`);
      await settle(mockup);

      for (const [name, selector, text] of PARTS) {
        const mine = await seen(page, selector);
        const theirs = await seen(mockup, selector);
        expect.soft(mine, `${name} (${selector})`).not.toBeNull();
        if (!mine || !theirs) continue;
        expect.soft(mine.look, `${name} (${selector}) look`).toEqual(theirs.look);
        expect.soft(near(mine.box, theirs.box), `${name} (${selector}) box ${JSON.stringify([mine.box, theirs.box])}`).toBe(true);
        if (text) expect.soft(mine.text, `${name} (${selector}) text`).toBe(theirs.text);
      }

      // The shortcut is ⌘K on macOS only: compare its look, not its text.
      const kbd = [await seen(page, ".search kbd"), await seen(mockup, ".search kbd")];
      expect.soft(kbd[0]?.look).toEqual(kbd[1]?.look);
      expect.soft(near(kbd[0]?.box ? { ...kbd[0].box, x: 0, width: 0 } : null, kbd[1]?.box ? { ...kbd[1].box, x: 0, width: 0 } : null)).toBe(true);

      // The footer under Home: same look and words, wherever Home ends.
      const foot = [await seen(page, ".foot"), await seen(mockup, ".foot")];
      expect.soft(foot[0]?.look).toEqual(foot[1]?.look);
      expect.soft(foot[0]?.text).toBe(foot[1]?.text);
      expect.soft(foot[0]?.box?.width).toBe(foot[1]?.box?.width);
      await mockup.close();
    });
  });
}

// The assistant's panel (#140) with the fixture's opening conversation, beside Home at 1440 px
// and on its own page at 390 px. The mockup's composer ends with a "static preview" line ours
// doesn't have, so its parts are compared by look and width, not by height.
const FEED: [string, string][] = [
  ["ping", "#feed .ping"],
  ["ping label", "#feed .ping small"],
  ["ping line", "#feed .ping b"],
  ["question", "#feed .msg.me"],
  ["answer", "#feed .msg.bot"],
];
const COMPOSER: [string, string][] = [
  ["feed", "#feed"],
  ["chips", "#chips"],
  ["chip", "#chips .pill"],
  ["input box", ".box-in"],
  ["input", ".box-in textarea"],
  ["send", ".send"],
];

for (const [width, path, hash] of [
  [1440, "", "#home"],
  [390, "/assistant", "#assistant"],
] as const) {
  test.describe(`assistant at ${width} px`, () => {
    test.skip(!existsSync(MOCKUP), `mockup not found: ${MOCKUP}`);
    test.use({ viewport: { width, height: 900 } });

    test("matches the mockup with the fixture's conversation", async ({ page, browser }) => {
      await page.clock.install({ time: DEMO_NOW });
      await page.goto(`/manage/la-graine-verte${path}`);
      await expect(page.locator("#feed .msg.me")).toHaveCount(2);
      await expect(page.locator("#chips .pill")).toHaveCount(4);
      await settle(page);

      const mockup = await browser.newPage({ viewport: { width, height: 900 } });
      await mockup.goto(`${pathToFileURL(MOCKUP).href}${hash}`);
      await settle(mockup);

      for (const [name, selector] of FEED) {
        const mine = await seen(page, selector);
        const theirs = await seen(mockup, selector);
        expect.soft(mine, `${name} (${selector})`).not.toBeNull();
        if (!mine || !theirs) continue;
        expect.soft(mine.look, `${name} look`).toEqual(theirs.look);
        expect.soft(near(mine.box, theirs.box), `${name} box ${JSON.stringify([mine.box, theirs.box])}`).toBe(true);
        expect.soft(mine.text, `${name} text`).toBe(theirs.text);
      }
      for (const [name, selector] of COMPOSER) {
        const mine = await seen(page, selector);
        const theirs = await seen(mockup, selector);
        expect.soft(mine, `${name} (${selector})`).not.toBeNull();
        if (!mine || !theirs) continue;
        expect.soft(mine.look, `${name} look`).toEqual(theirs.look);
        expect.soft([mine.box?.x, mine.box?.width], `${name} x and width`).toEqual([theirs.box?.x, theirs.box?.width]);
      }
      // The whole conversation reads the same.
      const conversation = (p: Page) => p.locator("#feed").innerText();
      expect.soft(await conversation(page)).toBe(await conversation(mockup));
      const chips = (p: Page) => p.locator("#chips .pill").allInnerTexts();
      expect.soft(await chips(page)).toEqual(await chips(mockup));
      await mockup.close();
    });
  });
}

test.describe("shell behaviour in a browser", () => {
  test("the assistant follows a resize across 1240 px", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/manage/la-graine-verte/rules");
    const panel = page.locator("aside.asst");
    await expect(panel).toBeVisible();
    await page.setViewportSize({ width: 1100, height: 900 });
    await expect(panel).toBeHidden();
    await page.setViewportSize({ width: 1300, height: 900 });
    await expect(panel).toBeVisible();
  });

  test("Cmd or Ctrl K focuses the search", async ({ page }) => {
    await page.goto("/manage/la-graine-verte/rules");
    await expect(page.locator(".brand-sub")).toHaveText("Mulhouse, €");
    await page.keyboard.press("ControlOrMeta+k");
    await expect(page.getByRole("textbox", { name: "Search a guest, a booking or a page" })).toBeFocused();
  });

  test("a page change goes through a view transition", async ({ page }) => {
    await page.goto("/manage/la-graine-verte");
    await page.evaluate(() => {
      const doc = document as Document & { startViewTransition: (cb: () => void) => unknown };
      const start = doc.startViewTransition.bind(doc);
      (window as unknown as { transitions: number }).transitions = 0;
      doc.startViewTransition = (cb) => {
        (window as unknown as { transitions: number }).transitions += 1;
        return start(cb);
      };
    });
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Floor plan" }).click();
    await expect(page).toHaveTitle("La Graine Verte · Floor plan");
    expect(await page.evaluate(() => (window as unknown as { transitions: number }).transitions)).toBeGreaterThan(0);
  });
});
