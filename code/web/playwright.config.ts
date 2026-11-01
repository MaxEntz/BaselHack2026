import { defineConfig, devices } from "@playwright/test";

const PORT = 5179;
// The visual tests (e2e/visual.spec.ts) take the mock built as shipped: quicker to open than the
// dev server's first load of every page at once, and the CSS as it goes out.
const VISUAL = !!process.env.SESAME_VISUAL;

// Browser tests (e2e/). `npm run test:e2e` starts Vite in mock mode on its own port.
export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? "github" : "list",
  // Visual baselines (e2e/visual.spec.ts): one set, taken in Playwright's image as CI runs them.
  snapshotPathTemplate: "{testDir}/__screenshots__/{arg}{ext}",
  expect: { toHaveScreenshot: { maxDiffPixels: 50 } },
  use: { baseURL: `http://localhost:${PORT}`, viewport: { width: 1440, height: 900 } },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: {
    command: VISUAL
      ? `npx vite build --outDir dist-visual --emptyOutDir && npx vite preview --outDir dist-visual --port ${PORT} --strictPort`
      : `npx vite --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    env: { VITE_MOCK: "1" },
    reuseExistingServer: !process.env.CI && !VISUAL,
    timeout: 120_000,
  },
});
