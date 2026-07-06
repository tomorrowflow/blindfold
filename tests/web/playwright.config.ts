import { defineConfig } from "@playwright/test";

const PORT = 8951;

export default defineConfig({
  testDir: "./specs",
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
  },
  webServer: {
    command: "uv run python serve_fixture.py",
    cwd: __dirname,
    url: `http://127.0.0.1:${PORT}/ui/org-graph`,
    reuseExistingServer: false,
    timeout: 20_000,
  },
});
