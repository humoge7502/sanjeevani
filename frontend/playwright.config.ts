import { defineConfig, devices } from "@playwright/test";

/**
 * Browser test configuration.
 *
 * These specs are the only place in the project where the product is exercised
 * the way a presenter actually uses it: a real Chromium renderer, real clicks, a
 * real backend. Everything else (Vitest, pytest) tests logic below the DOM.
 *
 * Ports are deliberately NOT the demo ports. `scripts/dev.sh` serves 8787/5173,
 * and copying those here would mean the suite silently tested whatever dev
 * server happened to be running instead of the code under test. If a stale
 * server is up on the demo ports, these tests are unaffected.
 */
const API_PORT = 8788;
const WEB_PORT = 5174;

const API_URL = `http://127.0.0.1:${API_PORT}`;
const WEB_URL = `http://127.0.0.1:${WEB_PORT}`;

export default defineConfig({
  testDir: "./e2e",
  // Vitest owns `src/**/*.test.ts`. Keep the two runners disjoint.
  testIgnore: ["**/node_modules/**", "**/dist/**"],

  fullyParallel: false,
  // The backend holds one mutable demo state (ledger, approvals, SAP mocks).
  // Specs therefore run serially; parallel workers would race on that state.
  workers: 1,
  forbidOnly: Boolean(process.env["CI"]),
  retries: 0,

  timeout: 45_000,
  expect: { timeout: 10_000 },

  reporter: process.env["CI"] ? [["list"], ["html", { open: "never" }]] : [["list"]],

  use: {
    baseURL: WEB_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },

  projects: [
    {
      name: "chromium-desktop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1600, height: 1000 } },
    },
    {
      // The brief requires the journey to survive realistic screen sizes and
      // that status never be conveyed by colour alone. A narrow viewport is
      // where a dense enterprise layout usually breaks first.
      name: "chromium-narrow",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1120, height: 800 } },
    },
  ],

  webServer: [
    {
      command:
        "python3 -m uvicorn backend.api.app:app --host 127.0.0.1 " +
        `--port ${API_PORT} --log-level warning`,
      cwd: "..",
      url: `${API_URL}/api/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: `npx vite --host 127.0.0.1 --port ${WEB_PORT} --strictPort`,
      env: {
        SANJEEVANI_API_TARGET: API_URL,
        VITE_PORT: String(WEB_PORT),
      },
      url: WEB_URL,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
});
