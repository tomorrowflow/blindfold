import { defineConfig } from "@playwright/test";

const PORT = 8951;
// Second fixture instance (issue #96): same `serve_fixture.py`, left at its honest
// unconfigured-L3 default (`BLINDFOLD_FIXTURE_STATE=degraded`) instead of the
// force-all-healthy default the primary instance uses — so the Home/Status
// Degraded-render specs drive a real fail-closed condition, not a stub.
const DEGRADED_PORT = 8952;
// Third fixture instance (issue #107): a genuinely empty store (no workspace, no
// entity, no RBAC grant) — the setup-shell spec's forced-redirect and
// create-first-workspace/creator-becomes-admin flow need real empty-store state.
const EMPTY_PORT = 8953;
// Fourth and fifth fixture instances (issue #108, Setup slice 5/5; reworked by
// #109 to decouple create from populate): two more independent genuinely-empty
// stores for entity-list-populate.spec.ts's one-click Sample data (via Setup's
// checkbox) and Seed bundle Import (via the entity list's empty state) specs
// respectively — each of those creates its own workspace and self-grants admin
// only when the store was empty beforehand (issue #107's privilege-escalation
// guard), so the two specs need two stores of their own, kept separate from each
// other and from EMPTY_PORT (setup-shell.spec.ts).
const SAMPLE_DATA_EMPTY_PORT = 8954;
const IMPORT_BUNDLE_EMPTY_PORT = 8955;

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
      url: `http://127.0.0.1:${PORT}/ui/`,
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
    {
      command: "uv run python serve_fixture.py",
      cwd: __dirname,
      url: `http://127.0.0.1:${EMPTY_PORT}/ui/`,
      reuseExistingServer: false,
      timeout: 20_000,
      env: {
        BLINDFOLD_FIXTURE_PORT: String(EMPTY_PORT),
        BLINDFOLD_FIXTURE_STATE: "empty",
      },
    },
    {
      command: "uv run python serve_fixture.py",
      cwd: __dirname,
      url: `http://127.0.0.1:${SAMPLE_DATA_EMPTY_PORT}/ui/`,
      reuseExistingServer: false,
      timeout: 20_000,
      env: {
        BLINDFOLD_FIXTURE_PORT: String(SAMPLE_DATA_EMPTY_PORT),
        BLINDFOLD_FIXTURE_STATE: "empty",
      },
    },
    {
      command: "uv run python serve_fixture.py",
      cwd: __dirname,
      url: `http://127.0.0.1:${IMPORT_BUNDLE_EMPTY_PORT}/ui/`,
      reuseExistingServer: false,
      timeout: 20_000,
      env: {
        BLINDFOLD_FIXTURE_PORT: String(IMPORT_BUNDLE_EMPTY_PORT),
        BLINDFOLD_FIXTURE_STATE: "empty",
      },
    },
  ],
});
