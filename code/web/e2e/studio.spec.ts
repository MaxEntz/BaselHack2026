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
    await expect(page.getByText("Found 8 details.")).toBeVisible();
    await expect(page.getByText("Waiting for you.")).toBeVisible();
    await expect(page.getByText("I read your page. Tap a line")).toBeVisible();
    await expect(page.getByText("Check these")).toBeVisible();
    await expect(page.getByText("Your private link: keep it")).toBeVisible();
    await expect(page.getByText("Your agent is built and hidden from guests.")).toHaveCount(0);

    const fact = page.getByRole("button", { name: /Largest group/ });
    await fact.click();
    await expect(fact).toHaveAttribute("aria-pressed", "true");

    const frame = page.frameLocator(".st-bw iframe").locator("body");
    await expect(frame).toBeAttached();
    await page.getByRole("button", { name: "Agent site" }).first().click();
    await expect(page.getByText("Agent site, no interface")).toBeVisible();
    // The management site stays loaded behind it, so a draft in a form survives.
    await expect(frame).toBeAttached();
    await expect(page.locator(".st-bw iframe")).toBeHidden();
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

  test("the page read shows during the build, and its facts light up one after the other", async ({ page }) => {
    await giveAddress(page);
    await page.evaluate(() => {
      const tags: string[] = [];
      (window as unknown as { tags: string[] }).tags = tags;
      const note = () => {
        const tag = document.querySelector(".st-sel")?.getAttribute("data-tag");
        if (tag && tags.at(-1) !== tag) tags.push(tag);
        requestAnimationFrame(note);
      };
      note();
    });
    await page.getByRole("button", { name: "Skip this, use the defaults" }).click();
    await expect(page.getByText("Your public site, as read")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("Images and web fonts aren't loaded")).toBeVisible();
    await expect(page.getByRole("button", { name: "Your public site" })).toBeEnabled();
    await expect(page.getByRole("button", { name: "Both" })).toBeDisabled();

    await expect(page.getByText("Agent site, no interface")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("Management site", { exact: true }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Both" })).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator(".st-sel")).toHaveCount(0);
    const tags = await page.evaluate(() => (window as unknown as { tags: string[] }).tags);
    expect(tags).toEqual(["Name", "Hours", "Tags", "Largest group", "Cancelling", "Price level"]);
  });

  test("a fact shows where it came from, until the next action", async ({ page }) => {
    await buildSite(page);
    await expect(page.getByRole("button", { name: "Both" })).toHaveAttribute("aria-pressed", "true", { timeout: 10_000 });
    await page.getByRole("button", { name: /Largest group/ }).click();
    const selection = page.locator(".st-sel");
    await expect(selection).toHaveAttribute("data-tag", "Largest group");
    await expect(selection).toBeInViewport();
    await expect(page.getByRole("button", { name: "Your public site" })).toHaveAttribute("aria-pressed", "true");
    await expect(selection).toHaveCSS("outline-color", "rgb(63, 105, 255)");
    await page.getByRole("button", { name: /Management$/ }).click();
    await expect(selection).toHaveCount(0);
  });

  test("the agent tab shows its session, and a button sends a frame", async ({ page }) => {
    await buildSite(page);
    await page.getByRole("button", { name: "Agent site" }).last().click();
    await expect(page.getByRole("button", { name: "Agent site" }).last()).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator(".st-zoom")).toHaveText(/^\d+%$/);
    const log = page.locator(".st-w-log");
    await expect(log).toContainText('"type":"hello"');
    await expect(log).toContainText('"token":"…"');
    await expect(page.getByText("What Claude reads through the MCP bridge")).toBeVisible();
    await expect(page.getByText("get_availability")).toBeVisible();
    await expect(page.getByText("When the agent can't answer")).toBeVisible();

    await page.getByRole("button", { name: "PING" }).click();
    await expect(log).toContainText('"intent":"PING"');
    await expect(log.locator(".st-w-s.ok").last()).toHaveText("200");
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
    // Every frame until the split settles: when, where the canvas starts, and the chat column's width.
    await page.evaluate(() => {
      const samples: { at: number; left: number; chat: number }[] = [];
      (window as unknown as { samples: typeof samples }).samples = samples;
      const body = document.querySelector(".st-body")!;
      const sample = (at: number) => {
        const canvas = document.querySelector(".st-canvas");
        const chat = document.querySelector(".st-chat");
        if (canvas && chat) {
          samples.push({
            at,
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
      () => (window as unknown as { samples: { at: number; left: number; chat: number }[] }).samples,
    );
    const lefts = samples.map((sample) => sample.left);
    const end = 76 + 1440 * 0.4;
    // It starts at the right edge, only moves left, and settles at 76 + 40 % of the width left.
    expect(lefts[0]).toBeGreaterThan(1200);
    for (let i = 1; i < lefts.length; i++) {
      expect(lefts[i]).toBeLessThanOrEqual(lefts[i - 1] + 0.5);
      // Bounded by time, not by frame: a busy runner drops frames, a jump doesn't take any.
      const pxPerMs = (lefts[i - 1] - lefts[i]) / (samples[i].at - samples[i - 1].at);
      expect(pxPerMs).toBeLessThan(160 / 16.7);
    }
    expect(lefts.filter((left) => left < lefts[0] - 1 && left > end + 1).length).toBeGreaterThanOrEqual(5);
    expect(lefts.at(-1)).toBeCloseTo(end, -1);
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
    await expect(page.getByRole("button", { name: "Both" })).toBeHidden();
    await expect(page.locator(".st-zoom")).toBeHidden();

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
