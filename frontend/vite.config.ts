import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The frontend never talks to a remote service: the only target is a local
// backend. There is no cloud endpoint in this project, which is what makes the
// offline-first demo claim honest rather than aspirational.
//
// The default matches `config/settings.yaml` (`api.port: 8787`). The env override
// exists so the browser test suite can run the whole stack on isolated ports
// without fighting a dev server that is already up. Production and demo runs
// never set it, so the default is what ships.
const LOCAL_API_TARGET = process.env["SANJEEVANI_API_TARGET"] ?? "http://127.0.0.1:8787";
const WEB_PORT = Number(process.env["VITE_PORT"] ?? 5173);

export default defineConfig({
  plugins: [react()],
  server: {
    port: WEB_PORT,
    host: "127.0.0.1",
    proxy: {
      "/api": {
        target: LOCAL_API_TARGET,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  test: {
    globals: true,
    environment: "node",
    include: ["src/**/*.test.ts"],
    // Playwright owns `e2e/`. Vitest must never try to run those specs, and
    // Playwright must never try to run these — the `testDir` split keeps the
    // two runners from colliding.
    exclude: ["e2e/**", "node_modules/**", "dist/**"],
    // `css: true` makes Vitest return real CSS text for stylesheet imports
    // instead of an empty stub. The accessibility guard in `src/a11y.test.ts`
    // imports `tokens.css?raw` and `app.css?raw`, so this keeps that guard
    // honest rather than vacuously passing against ''.
    css: true,
  },
});
