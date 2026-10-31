import { type Page, expect, test } from "@playwright/test";

// Studio against the mock (#141): the whole flow at 1440 and 390 px, then the split's motion.
const HOST = "lagraineverte.example";
const LIVE = /You're live\. Agents get real answers now\./;

async function giveAddress(page: Page) {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "What's your website's address?" })).toBeVisible();
  await page.getByRole("textbox", { name: "Message" }).fill(HOST);
  await page.keyboard.press("Enter");
  await expect(page.getByText(`${HOST}`, { exact: true }).first()).toBeVisible();
}

async function buildSite(page: Page) {
  await giveAddress(page);
  await page.getByRole("button", { name: "Skip this, use the defaults" }).click();
  await expect(page.getByText("What went into your profile")).toBeVisible({ timeout: 20_000 });
  // The turn is over: the top bar's Publish opens.
  await expect(page.getByRole("button", { name: "Publish", exact: true }).first()).toBeEnabled();
}

test.describe("at 1440 px", () => {
  test("builds, reviews and publishes a site", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("textbox", { name: "Message" })).toHaveAttribute(
      "placeholder",
      "Paste your website address and tell me how you work. 40 seats, groups up to 8…",
    );
    await expect(page.getByRole("navigation", { name: "Restaurants" }).getByText("Examples")).toBeVisible();
    await expect(page.getByRole("button", { name: "Publish" })).toHaveCount(0);

    await giveAddress(page);
    await expect(page.getByText("Websites rarely say how the room works")).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Message" })).toHaveAttribute(
      "placeholder",
      "Seats, tables, the largest group you take",
    );
    await expect(page.locator(".st-status")).toHaveText("Not built yet");

    await page.getByRole("button", { name: "Skip this, use the defaults" }).click();
    await expect(page.getByText(`Building ${HOST}`)).toBeVisible();
    await expect(page.getByText("Reading your site")).toBeVisible();
    await expect(page.getByText("Read 1 page: /")).toBeVisible();

    await expect(page.getByText("What went into your profile")).toBeVisible({ timeout: 20_000 });
    await expect(page.locator(".st-status")).toHaveText("Hidden from guests");
    await expect(page.getByText("Building La Graine Verte")).toBeVisible();
    await expect(page.getByText("Found 7 details.")).toBeVisible();
    await expect(page.getByText("Waiting for you.")).toBeVisible();
    await expect(page.getByText("I read your page. Tap a line")).toBeVisible();
    await expect(page.getByText("Check these")).toBeVisible();
    await expect(page.getByText("Your private link: keep it")).toBeVisible();
    await expect(page.getByText("Your agent is built and hidden from guests.")).toHaveCount(0);

    const fact = page.getByRole("button", { name: /Largest group/ });
    await fact.click();
    await expect(fact).toHaveAttribute("aria-pressed", "true");

    const frame = page.frameLocator(".st-sheet iframe").locator("body");
    await expect(frame).toBeAttached();
    await page.getByRole("button", { name: "Agent site" }).first().click();
    await expect(page.getByText("Agent site, no interface")).toBeVisible();
    // The management site stays loaded behind it, so a draft in a form survives.
    await expect(frame).toBeAttached();
    await expect(page.locator(".st-sheet iframe")).toBeHidden();
    await expect(page.getByText("Right now every agent gets a 404.")).toBeVisible();

    const publish = page.getByRole("button", { name: "Publish", exact: true }).first();
    await expect(publish).toBeEnabled();
    await publish.click();
    await expect(page.getByText(LIVE)).toBeVisible();
    await expect(page.locator(".st-status")).toHaveText("Live");
    await expect(page.getByRole("button", { name: "Published" })).toBeDisabled();
    await expect(page.getByText("Your addresses")).toBeVisible();
    await expect(page.getByText("Agent site, for any agent")).toBeVisible();
    await expect(page.getByText("Agents get real answers now.").first()).toBeVisible();
    await expect(page.getByRole("button", { name: "How busy are we tonight?" })).toBeVisible();
  });

  test("an address with a description goes straight to building", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("textbox", { name: "Message" }).fill(`${HOST}, 24 seats and groups up to 4`);
    await page.keyboard.press("Enter");
    await expect(page.getByText("Websites rarely say how the room works")).toHaveCount(0);
    await expect(page.getByText("What went into your profile")).toBeVisible({ timeout: 20_000 });
  });

  test("New starts over, once the merchant agreed to leave the site", async ({ page }) => {
    await buildSite(page);
    const asked: string[] = [];
    let accept = false;
    page.on("dialog", (dialog) => {
      asked.push(dialog.message());
      void (accept ? dialog.accept() : dialog.dismiss());
    });
    const rail = page.getByRole("navigation", { name: "Restaurants" });
    await rail.getByRole("button", { name: "New" }).click();
    await expect(page.locator(".st-status")).toHaveText("Hidden from guests");
    expect(asked[0]).toContain("La Graine Verte stays reachable through its private link");
    accept = true;
    await page.getByRole("navigation", { name: "Restaurants" }).getByRole("button", { name: "New" }).click();
    await expect(page.getByRole("heading", { name: "What's your website's address?" })).toBeVisible();
    await expect(page.locator(".st-canvas")).toHaveCount(0);
    await expect(page.locator(".st-status")).toHaveCount(0);
  });

  test("the canvas slides in with no jump, and nothing pops into the feed", async ({ page }) => {
    await giveAddress(page);
    // Every frame until the split settles: where the canvas starts, and the chat column's width.
    await page.evaluate(() => {
      const samples: { left: number; chat: number }[] = [];
      (window as unknown as { samples: typeof samples }).samples = samples;
      const body = document.querySelector(".st-body")!;
      const sample = () => {
        const canvas = document.querySelector(".st-canvas");
        const chat = document.querySelector(".st-chat");
        if (canvas && chat) {
          samples.push({
            left: canvas.getBoundingClientRect().left,
            chat: chat.getBoundingClientRect().width,
          });
        }
        if (samples.length < 90 && body.isConnected) requestAnimationFrame(sample);
      };
      requestAnimationFrame(sample);
    });
    await page.getByRole("button", { name: "Skip this, use the defaults" }).click();
    await expect(page.getByText("What went into your profile")).toBeVisible({ timeout: 20_000 });
    await expect
      .poll(() => page.evaluate(() => (window as unknown as { samples: unknown[] }).samples.length), { timeout: 10_000 })
      .toBeGreaterThanOrEqual(90);
    const samples = await page.evaluate(
      () => (window as unknown as { samples: { left: number; chat: number }[] }).samples,
    );
    const lefts = samples.map((sample) => sample.left);
    // It starts at the right edge, only moves left, and settles at 76 + 40 % of the width left.
    expect(lefts[0]).toBeGreaterThan(1200);
    for (let i = 1; i < lefts.length; i++) {
      expect(lefts[i]).toBeLessThanOrEqual(lefts[i - 1] + 0.5);
      expect(lefts[i - 1] - lefts[i]).toBeLessThan(160);
    }
    expect(lefts.at(-1)).toBeCloseTo(76 + 1440 * 0.4, -1);
    const message = page.locator(".st-msg").last();
    await expect(message).toHaveCSS("animation-name", "st-rise");
  });
});

test.describe("at 390 px", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("one pane at a time", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator(".st-rail")).toBeHidden();
    await expect(page.getByRole("button", { name: "New" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Conversation" })).toHaveCount(0);
    const noScroll = () => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
    expect(await noScroll()).toBe(true);

    await buildSite(page);
    expect(await noScroll()).toBe(true);
    const conversation = page.getByRole("button", { name: "Conversation" });
    await expect(conversation).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator(".st-canvas")).toBeHidden();
    await expect(page.locator(".st-feed")).toBeVisible();

    await page.getByRole("button", { name: "Your sites" }).click();
    await expect(page.locator(".st-canvas")).toBeVisible();
    await expect(page.locator(".st-feed")).toBeHidden();

    await conversation.click();
    await page.getByRole("button", { name: "Publish", exact: true }).first().click();
    await expect(page.getByText(LIVE)).toBeVisible();
    await expect(page.getByRole("button", { name: "Published" })).toBeDisabled();
    expect(await noScroll()).toBe(true);
  });
});

test.describe("with reduced motion", () => {
  test.use({ reducedMotion: "reduce" });

  test("the split opens at once", async ({ page }) => {
    await buildSite(page);
    const box = await page.locator(".st-canvas").boundingBox();
    expect(box?.x).toBeCloseTo(76 + 1440 * 0.4, -1);
    await expect(page.locator(".st-msg").last()).toHaveCSS("animation-name", "none");
  });
});
