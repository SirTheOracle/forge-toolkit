import { defineConfig, devices } from "@playwright/test";

// BROWSER PROVISIONING, stated plainly (review R16). Headless does NOT remove the browser
// requirement. `npm run e2e:install` runs `playwright install --only-shell chromium`, which
// downloads chromium-headless-shell into the Playwright cache OUTSIDE this repository
// (~/Library/Caches/ms-playwright on macOS, ~/.cache/ms-playwright on Linux).
//
// `channel` is deliberately NOT set: naming a channel ("chromium", "chrome") selects the
// full browser build and the headless-shell download would not satisfy it.
//
// THE E2E SERVER IS A FIXTURE CHECKOUT, NOT THIS ONE. `scripts/e2e-server.mjs` (Step 65)
// builds a disposable checkout under .e2e-checkout/ with STUB forge binaries and a fixture
// project root, then runs THAT checkout's bin/forge-web. An e2e run therefore exercises the
// real launcher, the real backend and the real static mount while being incapable of
// touching the operator's tmux server, registry, watcher cache or any real .dev/.
const PORT = process.env.FORGE_WEB_E2E_PORT ?? "8799";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "on-first-retry",
    headless: true,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], channel: undefined } }],
  webServer: {
    command: "node scripts/e2e-server.mjs",
    url: `http://127.0.0.1:${PORT}/api/fleet`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      FORGE_WEB_E2E_PORT: PORT,
      FORGE_WEB_ALLOW_TEST_OVERRIDES: "1",
      FORGE_WATCH_TRIGGER: "0",
    },
  },
});