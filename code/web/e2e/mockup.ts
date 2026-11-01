import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";

/** The v10 mockup the comparisons read; SESAME_MOCKUP points to another copy. */
export const MOCKUP = process.env.SESAME_MOCKUP ?? resolve(import.meta.dirname, "../../../documentation/design/poc-v10/sesame-management.html");

/** Whether to skip the comparisons with the mockup: only locally. The mockup is tracked, so in
 * CI a missing one is an error, not a skip that would pass for a green run. */
export function mockupMissing(): boolean {
  if (existsSync(MOCKUP)) return false;
  if (process.env.CI) throw new Error(`mockup not found: ${MOCKUP}, and CI must compare with it`);
  return true;
}

// The mockup's olive (#727f55, band #6c744d) reads under 4.5:1 below text, so tokens.css
// darkens it (#153). The comparisons with the mockup give it our olive, read from tokens.css:
// every other colour, size and font must still match.
const TOKENS = readFileSync(resolve(import.meta.dirname, "../src/management/tokens.css"), "utf8");
const OLIVE = ["--olive", "--olive-band"].map((name) => {
  const value = new RegExp(`${name}: (#[0-9a-f]{6});`).exec(TOKENS)?.[1];
  if (!value) throw new Error(`${name} not found in tokens.css`);
  return `${name}: ${value};`;
});

/** Opens the mockup at `url` in `page`, with our olive. */
export async function openMockup(page: Page, url: string): Promise<void> {
  await page.goto(url);
  await page.addStyleTag({ content: `:root:root { ${OLIVE.join(" ")} }` });
}
