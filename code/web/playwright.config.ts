import { defineConfig, devices } from "@playwright/test";

const PORT = 5179;

// Browser tests (e2e/). `npm run test:e2e` starts Vite in mock mode on its own port.
export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? "github" : "list",
  use: { baseURL: `http://localhost:${PORT}`, viewport: { width: 1440, height: 900 } },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: {
    command: `npx vite --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    env: { VITE_MOCK: "1" },
    reuseExistingServer: !process.env.CI,
  },
});
