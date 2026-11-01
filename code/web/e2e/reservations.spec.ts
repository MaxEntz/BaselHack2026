import { pathToFileURL } from "node:url";
import { type Page, expect, test } from "@playwright/test";
import { MOCKUP, mockupMissing, openMockup } from "./mockup.ts";

// The Reservations page (#139) beside the v10 mockup, at 1440 and 390 px, with the demo fixture
// and its clock at 19:40; then add, move, cancel and block against the mock client.
const DEMO_NOW = new Date("2026-10-31T19:40:00+01:00");
const PAGE = "/manage/la-graine-verte/reservations";

const LOOK = [
  "font-family", "font-size", "font-weight", "font-style", "line-height", "color", "background-color",
  "padding-top", "padding-right", "padding-bottom", "padding-left", "border-top-left-radius", "display",
] as const;

// [what, selector in both pages, compare its text]
const PARTS: [string, string, boolean][] = [
  ["title", ".ttl h1", true],
  ["day", ".date", true],
  ["services", 'main .pills[aria-label="Service"]', true],
  ["summary", ".bar-row > p.num", true],
  ["filters", '.pills[aria-label="Booked by"]', true],
  ["actions", ".pills.right", true],
  ["columns", ".cols", true],
  ["first column", ".col", true],
  ["second column", ".col:nth-child(2)", true],
  ["time blob", ".time-blob", true],
  ["time", ".time-blob b", true],
  ["column title", ".col h3", true],
  ["guest row", ".who", true],
  ["guest name", ".who-n", true],
  ["party and table", ".who-t", true],
  ["agent note", ".who small.c", true],
];

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
  test.describe(`reservations at ${width} px`, () => {
    test.skip(mockupMissing(), `mockup not found: ${MOCKUP}`);
    test.use({ viewport: { width, height: 900 } });

    test("matches the mockup", async ({ page, browser }) => {
      await page.clock.install({ time: DEMO_NOW });
      await page.goto(PAGE);
      await expect(page.locator(".bar-row > p.num")).toHaveText("Tonight, 6 bookings and 17 guests.");
      await settle(page);

      const mockup = await browser.newPage({ viewport: { width, height: 900 } });
      await openMockup(mockup, `${pathToFileURL(MOCKUP).href}#reservations`);
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
      await mockup.close();
    });
  });
}

test.describe("reservations against the mock", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(PAGE);
    await expect(page.locator(".bar-row > p.num")).toHaveText("Tonight, 6 bookings and 17 guests.");
  });

  test("adds a booking", async ({ page }) => {
    await page.getByRole("button", { name: "New booking" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Guest's name").fill("Ana Roth");
    await expect(dialog.getByRole("status")).toHaveText("Table T4, 2 seats, until 22:00.");
    await dialog.getByRole("button", { name: "Add the booking" }).click();

    await expect(page.locator(".toast")).toContainText("Ana Roth is booked20:00, 2 guests, table T4");
    await expect(dialog).toBeHidden();
    await expect(page.locator(".bar-row > p.num")).toHaveText("Tonight, 7 bookings and 19 guests.");
    await expect(page.locator(".who", { hasText: "Ana Roth" })).toContainText("2, T4By you");
  });

  test("moves a booking", async ({ page }) => {
    await page.locator(".who", { hasText: "Marie Lang" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("combobox", { name: "Move to another time" })).toHaveValue("20:30");
    await dialog.getByRole("button", { name: "Move" }).click();

    await expect(page.locator(".toast")).toContainText("Marie Lang moved20:30, table T9");
    await expect(page.locator(".col", { hasText: "Marie Lang" }).locator(".time-blob b")).toHaveText("20:30");
    await expect(page).not.toHaveURL(/booking=/);
  });

  test("cancels a booking", async ({ page }) => {
    await page.locator(".who", { hasText: "Marie Lang" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Cancel the booking" }).click();
    await expect(dialog.locator(".note")).toHaveText(
      "Cancel this booking? Marie Lang sees a notice the next time their assistant checks.",
    );
    await dialog.getByRole("button", { name: "Cancel the booking" }).last().click();

    await expect(page.locator(".toast")).toContainText("Marie Lang is cancelledTable T9 is free again.");
    await expect(page.locator(".who.off", { hasText: "Marie Lang" })).toContainText("Cancelled by you");
    await expect(page.locator(".bar-row > p.num")).toHaveText("Tonight, 5 bookings and 13 guests.");
  });

  test("blocks a time", async ({ page }) => {
    await page.getByRole("button", { name: "Block a time" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.locator("label", { hasText: "20:00" }).click();
    await dialog.getByRole("button", { name: "Save" }).click();

    await expect(page.locator(".toast")).toContainText("Blocked 20:00Agents stop offering it.");
    await expect(page.locator(".col.blocked h3")).toHaveText("3 bookings, 8 guests, blocked");
  });
});
