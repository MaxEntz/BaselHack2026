import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Component tests (Vitest + Testing Library, jsdom). Pure-logic tests stay on Node's own
// runner (tests/*.test.ts); browser tests are Playwright's (e2e/).
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["./src/test-setup.ts"],
    css: false,
  },
});
