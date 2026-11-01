import { type Locator, type Page, expect, test } from "@playwright/test";
import { PAGES, type PageId, pagePath } from "../src/management/pages.ts";

// Visual tests (#153): a screenshot of every management page and every Studio state at 1440
// and 390 px (the window, 900 px high), against the committed baselines in __screenshots__/.
// Against the mock, with the demo fixture and its clock at 19:40, stopped before the agent's live
// booking (the live moment moves, so it is left out). The pages are held to the mockup by shell, primitives and
// reservations.spec.ts; these hold them still.
//
// Fonts render differently from one system to another, so these run in Playwright's own image
// only, as CI does: `npm run test:visual:docker` checks, `-- --update-snapshots` retakes.
test.skip(!process.env.SESAME_VISUAL, "run by `npm run test:visual:docker`, in Playwright's image");

const SLUG = "la-graine-verte";
const DEMO_NOW = new Date("2026-10-31T19:40:00+01:00");
const HOST = "lagraineverte.example";

test.use({ reducedMotion: "reduce" });
// Each move of the stopped clock is a round trip to the browser: slower than the default 30 s allows.
test.describe.configure({ timeout: 90_000 });

/** The page's clock stands at 19:40, and moves only when `until` or `shoot` move it. */
async function stopped(page: Page) {
  // From a minute before: the clock runs between the two calls, and can't be set back.
  await page.clock.install({ time: DEMO_NOW.getTime() - 60_000 });
  await page.clock.pauseAt(DEMO_NOW);
}

/**
 * Moves the clock `step` ms at a time until `what` shows: the mock answers on timers. At most
 * `budget` ms in all, so that a slow machine can't carry a page to its live booking (6 s after
 * the page listens, `shoot` adding 1 s).
 */
async function until(page: Page, what: Locator, step = 250, budget = 4000) {
  for (let moved = 0; moved < budget; moved += step) {
    await page.clock.runFor(step);
    // A moment of real time for the page to render what the timers gave it.
    await page.waitForTimeout(100);
    if (await what.isVisible()) return;
  }
  throw new Error(`${what} not shown after ${budget} ms of the page's clock`);
}

/**
 * One more second for the last answers, fonts loaded, then the screenshot, with the clock still:
 * the agent's live booking, 6 s after the page listens, never comes. The window only: a full-page
 * shot grows the window, and the sidebar and assistant (100vh, sticky) grow with it.
 */
async function shoot(page: Page, name: string, mask: Locator[] = []) {
  await page.clock.runFor(1000);
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot(`${name}.png`, { animations: "disabled", caret: "hide", mask, timeout: 15_000 });
}

for (const width of [1440, 390]) {
  test.describe(`at ${width} px`, () => {
    test.use({ viewport: { width, height: 900 } });

    for (const id of Object.keys(PAGES) as PageId[]) {
      test(PAGES[id], async ({ page }) => {
        await stopped(page);
        await page.goto(pagePath(SLUG, id));
        await until(page, page.locator(".brand-sub", { hasText: "Mulhouse, €" }));
        await shoot(page, `${id}-${width}`);
      });
    }

    test("Studio", async ({ page }) => {
      // The build plays its steps and lights the facts up one by one: many moves of the clock.
      test.setTimeout(180_000);
      await stopped(page);
      await page.goto("/");
      const message = page.getByRole("textbox", { name: "Message" });
      await until(page, message);
      await shoot(page, `studio-empty-${width}`);

      await message.fill(HOST);
      await page.keyboard.press("Enter");
      const skip = page.getByRole("button", { name: "Skip this, use the defaults" });
      await until(page, skip);
      await shoot(page, `studio-asking-${width}`);

      await skip.click();
      // The management site in the canvas is masked below, so its live booking may come.
      await until(page, page.getByText("What went into your profile"), 500, 120_000);
      // It loads on its own time, too; its pages have their own shots.
      await shoot(page, `studio-built-${width}`, [page.locator(".st-bw iframe")]);
    });
  });
}
