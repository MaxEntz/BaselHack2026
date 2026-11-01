import AxeBuilder from "@axe-core/playwright";
import { type Page, expect, test } from "@playwright/test";
import { PAGES, type PageId, pagePath } from "../src/management/pages.ts";

// Accessibility (#153): axe's WCAG 2.2 AA rules on every management page and every Studio state
// at 1440 and 390 px, the keyboard paths through the nav, the dialogs and the search, a focus
// ring on whatever takes focus, and nothing moving with reduced motion. Against the mock, with
// the demo fixture and its clock at 19:40.
const SLUG = "la-graine-verte";
const DEMO_NOW = new Date("2026-10-31T19:40:00+01:00");
const HOST = "lagraineverte.example";
const WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

/** axe's violations, one line each, so a failure says what and where. */
async function violations(page: Page): Promise<string[]> {
  const { violations } = await new AxeBuilder({ page }).withTags(WCAG).analyze();
  return violations.flatMap((rule) =>
    rule.nodes.map((node) => `${rule.id}: ${node.target.join(" ")}: ${(node.failureSummary ?? "").replace(/\s+/g, " ")}`),
  );
}

/** Until the page stops growing: the mock answers each call after a delay. */
async function settled(page: Page) {
  let last = -1;
  await expect
    .poll(async () => {
      const now = await page.evaluate(() => document.querySelectorAll("*").length);
      const same = now === last;
      last = now;
      return same;
    }, { intervals: [300] })
    .toBe(true);
}

async function openPage(page: Page, id: PageId) {
  await page.clock.install({ time: DEMO_NOW });
  await page.goto(pagePath(SLUG, id));
  await expect(page.locator(".brand-sub")).toHaveText("Mulhouse, €");
  await expect(page.locator("h1, .asst h2").first()).toBeVisible();
  await settled(page);
}

async function giveAddress(page: Page) {
  await page.goto("/");
  await page.getByRole("textbox", { name: "Message" }).fill(HOST);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Skip this, use the defaults" })).toBeVisible();
}

// Text fades in; with reduced motion it is there at once, so axe reads its final colours.
test.use({ reducedMotion: "reduce" });

for (const width of [1440, 390]) {
  test.describe(`axe at ${width} px`, () => {
    test.use({ viewport: { width, height: 900 } });

    for (const id of Object.keys(PAGES) as PageId[]) {
      test(`${PAGES[id]}`, async ({ page }) => {
        await openPage(page, id);
        expect(await violations(page)).toEqual([]);
      });
    }

    test("Reservations' dialogs", async ({ page }) => {
      await openPage(page, "reservations");
      for (const name of ["Block a time", "New booking"]) {
        await page.getByRole("button", { name, exact: true }).click();
        await expect(page.getByRole("dialog")).toBeVisible();
        expect(await violations(page), name).toEqual([]);
        await page.keyboard.press("Escape");
        await expect(page.getByRole("dialog")).toBeHidden();
      }
    });

    test("Studio, from the first message to the built site", async ({ page }) => {
      await page.goto("/");
      await expect(page.getByRole("textbox", { name: "Message" })).toBeVisible();
      expect(await violations(page), "empty").toEqual([]);
      await giveAddress(page);
      expect(await violations(page), "asking").toEqual([]);
      await page.getByRole("button", { name: "Skip this, use the defaults" }).click();
      await expect(page.getByText("Reading your site")).toBeVisible();
      expect(await violations(page), "building").toEqual([]);
      await expect(page.getByText("What went into your profile")).toBeVisible({ timeout: 20_000 });
      expect(await violations(page), "built").toEqual([]);
    });
  });
}

/**
 * The focused element, marked as seen, and whether it or the box around it draws an outline of at
 * least 2 px. `again` once the focus comes back to an element already seen.
 */
async function focused(page: Page): Promise<{ what: string; ring: boolean; again: boolean; same: boolean }> {
  return page.evaluate(() => {
    const element = document.activeElement as HTMLElement | null;
    const memory = window as unknown as { a11yLast?: Element | null };
    // A time input keeps the focus while Tab goes from its hours to its minutes.
    const same = element === memory.a11yLast;
    memory.a11yLast = element;
    if (!element || element === document.body) return { what: "body", ring: false, again: false, same: false };
    if (element.tagName === "IFRAME") return { what: "iframe", ring: true, again: false, same };
    const again = element.dataset.a11ySeen === "1";
    element.dataset.a11ySeen = "1";
    const outlined = (node: Element) => {
      const style = getComputedStyle(node);
      return style.outlineStyle !== "none" && parseFloat(style.outlineWidth) >= 2;
    };
    // A radio draws its ring on its label, a composer's textarea on its box.
    const ring = [element, element.parentElement, element.parentElement?.parentElement].some((node) => node && outlined(node));
    const name = element.getAttribute("aria-label") ?? element.innerText?.trim().split("\n")[0] ?? "";
    return { what: `${element.tagName.toLowerCase()}.${element.className} "${name.slice(0, 40)}"`, ring, again, same };
  });
}

test.describe("keyboard", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  /** One full round of Tab from wherever the focus is, every element checked once. */
  async function everyStopRinged(page: Page) {
    await page.evaluate(() => document.querySelectorAll("[data-a11y-seen]").forEach((node) => node.removeAttribute("data-a11y-seen")));
    let stops = 0;
    let left = 0;
    for (let press = 0; press < 400; press++) {
      await page.keyboard.press("Tab");
      const { what, ring, again, same } = await focused(page);
      // Past the last stop the focus leaves the page, then comes back to the first. A site in
      // an iframe (Studio's canvas) has its own stops: its pages are walked on their own.
      if (what === "body") {
        if (++left === 2) break;
        continue;
      }
      if (what === "iframe" || same) continue;
      if (again) break;
      stops += 1;
      expect.soft(ring, what).toBe(true);
    }
    expect(stops).toBeGreaterThan(3);
    return stops;
  }

  for (const id of Object.keys(PAGES) as PageId[]) {
    test(`every stop on ${PAGES[id]} shows a focus ring`, async ({ page }) => {
      await openPage(page, id);
      await everyStopRinged(page);
    });
  }

  test("every stop in Studio shows a focus ring", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("textbox", { name: "Message" })).toBeVisible();
    await everyStopRinged(page);
    await giveAddress(page);
    await everyStopRinged(page);
    await page.getByRole("button", { name: "Skip this, use the defaults" }).click();
    await expect(page.getByText("What went into your profile")).toBeVisible({ timeout: 20_000 });
    await page.getByRole("group", { name: "Which site to show" }).getByRole("button", { name: /Management/ }).click();
    await expect(page.getByRole("group", { name: "Time of day" })).toBeVisible();
    await everyStopRinged(page);
  });

  test("Tab walks the nav in order, and Enter opens a page", async ({ page }) => {
    await openPage(page, "home");
    const nav = page.getByRole("navigation", { name: "Main" });
    const names = await nav.getByRole("link").allInnerTexts();
    const reached: string[] = [];
    for (let stop = 0; stop < 30 && reached.length < names.length; stop++) {
      await page.keyboard.press("Tab");
      const inNav = await nav.evaluate((element) => element.contains(document.activeElement));
      if (inNav) reached.push(await page.evaluate(() => (document.activeElement as HTMLElement).innerText));
    }
    expect(reached).toEqual(names);
    await nav.getByRole("link", { name: "Floor plan" }).focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveTitle("La Graine Verte · Floor plan");
  });

  test("a dialog keeps the focus inside, closes on Escape and gives it back", async ({ page }) => {
    await openPage(page, "reservations");
    const opener = page.getByRole("button", { name: "Block a time", exact: true });
    await opener.focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    // A modal leaves the page under it inert: Tab goes round the dialog, and past its last stop
    // to the browser's own controls (the page's body), never to the page.
    let inside = 0;
    for (let stop = 0; stop < 15; stop++) {
      await page.keyboard.press("Tab");
      const where = await dialog.evaluate((element) =>
        element.contains(document.activeElement) ? "dialog" : document.activeElement === document.body ? "browser" : "page",
      );
      expect(where).not.toBe("page");
      if (where === "dialog") inside += 1;
    }
    expect(inside).toBeGreaterThan(5);
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();
  });

  test("the search: the shortcut, Escape, Tab to the results, Enter opens the first", async ({ page }) => {
    await openPage(page, "home");
    const search = page.getByRole("textbox", { name: "Search a guest, a booking or a page" });
    await page.keyboard.press("ControlOrMeta+k");
    await expect(search).toBeFocused();
    await page.keyboard.type("Floor");
    await expect(page.getByRole("group", { name: "Search results" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("group", { name: "Search results" })).toBeHidden();
    await search.fill("Muller");
    const results = page.getByRole("group", { name: "Search results" });
    await expect(results.getByRole("button").first()).toBeVisible();
    await page.keyboard.press("Tab");
    await expect(results.getByRole("button").first()).toBeFocused();
    await search.focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveTitle("La Graine Verte · Reservations");
    await expect(page).toHaveURL(/booking=R-/);
  });
});

test.describe("reduced motion", () => {
  test("a page change and a dialog start no animation", async ({ page }) => {
    await openPage(page, "home");
    const running = () =>
      page.evaluate(() => document.getAnimations().filter((animation) => animation.playState === "running").length);
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Reservations" }).click();
    await expect(page).toHaveTitle("La Graine Verte · Reservations");
    expect(await running()).toBe(0);
    await page.getByRole("button", { name: "New booking", exact: true }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    expect(await running()).toBe(0);
  });

  test("Studio's build streams in with nothing moving", async ({ page }) => {
    await giveAddress(page);
    await page.getByRole("button", { name: "Skip this, use the defaults" }).click();
    await expect(page.getByText("Reading your site")).toBeVisible();
    const moving = await page.evaluate(() =>
      document.getAnimations().filter((animation) => animation.playState === "running").map((animation) => {
        const target = (animation.effect as KeyframeEffect | null)?.target;
        return target instanceof Element ? target.className.toString() : "?";
      }),
    );
    expect(moving).toEqual([]);
  });
});
