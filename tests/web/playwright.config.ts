import { defineConfig } from "@playwright/test";

const PORT = 8951;
// Second fixture instance (issue #96): same `serve_fixture.py`, left at its honest
// unconfigured-L3 default (`BLINDFOLD_FIXTURE_STATE=degraded`) instead of the
// force-all-healthy default the primary instance uses — so the Home/Status
// Degraded-render specs drive a real fail-closed condition, not a stub.
const DEGRADED_PORT = 8952;

export default defineConfig({
  testDir: "./specs",
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "uv run python serve_fixture.py",
      cwd: __dirname,
      url: `http://127.0.0.1:${PORT}/ui/org-graph`,
      reuseExistingServer: false,
      timeout: 20_000,
    },
    {
      command: "uv run python serve_fixture.py",
      cwd: __dirname,
      url: `http://127.0.0.1:${DEGRADED_PORT}/ui/org-graph`,
      reuseExistingServer: false,
      timeout: 20_000,
      env: {
        BLINDFOLD_FIXTURE_PORT: String(DEGRADED_PORT),
        BLINDFOLD_FIXTURE_STATE: "degraded",
      },
    },
  ],
});
