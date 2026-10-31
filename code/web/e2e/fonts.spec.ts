import { expect, test } from "@playwright/test";

test("fonts load with no external request", async ({ page, baseURL }) => {
  const origin = new URL(baseURL!).origin;
  const external: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.protocol.startsWith("http") && url.origin !== origin) external.push(request.url());
  });
  await page.goto("/?gallery");
  const loaded = await page.evaluate(async () => {
    await document.fonts.ready;
    return [...document.fonts].filter((face) => face.status === "loaded").map((face) => face.family.replaceAll('"', ""));
  });
  expect(loaded).toEqual(expect.arrayContaining(["Fraunces Soft", "Karla"]));
  expect(external).toEqual([]);
});

test("the management routes set their titles", async ({ page }) => {
  await page.goto("/manage/la-graine-verte/floor");
  await expect(page).toHaveTitle("La Graine Verte · Floor plan");
  await page.goto("/manage/la-graine-verte/nope");
  await expect(page).toHaveURL(/\/manage\/la-graine-verte$/);
  await expect(page).toHaveTitle("La Graine Verte · Home");
});

test("Studio runs at /", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "What's your website's address?" })).toBeVisible();
  await expect(page.getByRole("textbox")).toBeVisible();
});
