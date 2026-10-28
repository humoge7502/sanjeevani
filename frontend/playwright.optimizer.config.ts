import { defineConfig, devices } from "@playwright/test";

/**
 * The optimizer-mode browser suite.
 *
 * The main config (playwright.config.ts) boots the backend with the DEFAULT
 * plan provider, because that is what a presenter runs. But the optimizer is
 * implemented and opt-in, and its flagship surface -- the solver-trace panel,
 * the DES cross-check verdict, the counterfactuals table -- was until now
 * untested in a real renderer. This config boots the SAME app with
 * SANJEEVANI_PLAN_PROVIDER=optimizer on its own isolated ports (8789/5175) and
 * runs only the specs that assert the optimizer surfaces.
 *
 * Run: npm run test:browser:optimizer   (also folded into verify.sh step 8)
 */

const API_PORT = 8789;
const WEB_PORT = 5175;

const API_URL = `http://127.0.0.1:${API_PORT}`;
const WEB_URL = `http://127.0.0.1:${WEB_PORT}`;

export default defineConfig({
  testDir: "./e2e",
  testMatch: /optimizer\.spec\.ts/,

  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env["CI"]),
  retries: 0,

  timeout: 60_000,
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
  ],

  webServer: [
    {
      // `python`, not `python3` -- see playwright.config.ts for the Windows
      // Store-alias explanation.
      command:
        "python -m uvicorn backend.api.app:app --host 127.0.0.1 " +
        `--port ${API_PORT} --log-level warning`,
      cwd: "..",
      url: `${API_URL}/api/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
      env: {
        SANJEEVANI_PLAN_PROVIDER: "optimizer",
      },
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
