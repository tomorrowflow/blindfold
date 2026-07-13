import { promises as fs } from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { test, expect, request as pwRequest } from "@playwright/test";

// Populate is a persistent, decoupled step (issue #109, ADR-0029/ADR-0030): Setup's
// create screen only creates a workspace, with an opt-in "Load sample data"
// checkbox that populates the just-created workspace right after it exists; the
// entity list's empty state (any workspace, any time its entity count is zero)
// offers the same "Import a Seed bundle" / "Load sample data" actions as a
// persistent capability, not a first-run-only screen. Both paths go through the
// one shared `seed_entity_graph` path (ADR-0029) -- neither ever auto-creates a
// workspace of its own (no more auto-created `default`).
//
// Each describe block below runs against its OWN genuinely-empty serve_fixture.py
// instance (ports 8954/8955, BLINDFOLD_FIXTURE_STATE=empty, see
// playwright.config.ts) -- creating a workspace self-grants admin only when the
// store was empty beforehand (issue #107's privilege-escalation guard), so each
// spec needs a store of its own, independent of setup-shell.spec.ts's own
// empty-store instance too.
//
// The bundle fixture file is written under os.tmpdir(), NOT testInfo.outputPath():
// this file's describe titles carry an em dash ("—", matching this repo's other
// spec titles) and outputPath()'s sanitized-title directory silently breaks
// setInputFiles() when a sibling test's title also contains one -- Playwright then
// resolves the file but the browser attaches zero files, no error thrown (bisected
// by reproducing with a two-test file down to exactly this factor). A plain tmpdir
// path sidesteps it entirely.

const SAMPLE_DATA_BASE_URL = "http://127.0.0.1:8954";
const IMPORT_BUNDLE_BASE_URL = "http://127.0.0.1:8955";

test.describe("Setup — create with Load sample data checked", () => {
  test("ticking the checkbox pre-populates the just-created workspace, then lands on its entity list", async ({
    browser,
  }) => {
    const context = await browser.newContext({ baseURL: SAMPLE_DATA_BASE_URL });
    const page = await context.newPage();
    const requestHosts = new Set<string>();
    page.on("request", (req) => requestHosts.add(new URL(req.url()).host));

    await page.goto("/ui/setup");
    await page.getByTestId("setup-workspace-name").fill("Acme Corp");
    await page.getByTestId("setup-sample-checkbox").check();
    await page.getByTestId("setup-create-btn").click();

    await expect(page).toHaveURL(/\/ui\/entities$/);
    await expect(page.locator("h1")).toContainText("Entity list");
    // Pre-populated: the empty-state populate surface must NOT be showing.
    await expect(page.getByTestId("entity-list-empty-state")).toHaveCount(0);
    await expect(page.getByTestId("entity-table")).toBeVisible();

    // Egress hygiene: the whole click-to-populated-workspace round trip stays
    // same-origin — no browser egress of the bundle's real entity values (or
    // anything else) to a non-loopback host.
    const firstPartyHost = new URL(SAMPLE_DATA_BASE_URL).host;
    const thirdParty = [...requestHosts].filter((host) => host !== firstPartyHost);
    expect(thirdParty, `unexpected non-loopback requests: ${thirdParty.join(", ")}`).toEqual([]);
    await context.close();

    // The SPA never sends x-blindfold-identity (ADR-0019's static single-owner
    // model) — verify the grant landed server-side through the real roles
    // endpoint, exactly the way an authorized admin would query it.
    const api = await pwRequest.newContext({ baseURL: SAMPLE_DATA_BASE_URL });
    const rolesResp = await api.get("/v1/management/workspaces/acme-corp/roles", {
      headers: { "x-blindfold-identity": "" },
    });
    expect(rolesResp.status()).toBe(200);
    const roles = await rolesResp.json();
    expect(roles.assignments).toContainEqual({ identity: "", workspace: "acme-corp", role: "admin" });

    // Sample data never auto-creates `default` (issue #109 AC) — it loaded into
    // the explicit, already-created "acme-corp" workspace. No admin role was ever
    // granted on `default`, since it was never created via this flow.
    const defaultRolesResp = await api.get("/v1/management/workspaces/default/roles", {
      headers: { "x-blindfold-identity": "" },
    });
    expect(defaultRolesResp.status()).toBe(403);

    // The vendored bundle's 5 persons + 3 terms landed in the entity graph, in
    // surrogate space only (never a real name) — the same GET the entity-list
    // shell view uses.
    const entitiesResp = await api.get("/v1/management/workspaces/acme-corp/entities");
    const entities = (await entitiesResp.json()).entities as Array<{ active_surrogate: string }>;
    expect(entities).toHaveLength(8);
    for (const e of entities) {
      // None of the real vendored names ever appear surrogate-side.
      expect(e.active_surrogate).not.toBe("Martin Bach");
      expect(e.active_surrogate).not.toBe("Enervia");
    }
    await api.dispose();
  });
});

test.describe("Entity list — Import a Seed bundle (persistent populate surface)", () => {
  test("import is reachable on an existing workspace long after first run, mints local surrogates, never RBAC grants, and never auto-creates a workspace", async ({
    browser,
  }) => {
    const bundle = {
      workspace: { slug: "ignored-by-import", name: "Ignored By Import" },
      persons: [{ canonical_name: "Priya Shah", variations: ["Priya"] }],
      terms: [{ canonical_name: "Zenith Robotics", variations: [] }],
      // Not part of the ADR-0029 dictionary-only contract -- must be ignored.
      rbac_grants: [{ identity: "mallory", workspace: "acme-import", role: "admin" }],
    };
    const bundlePath = path.join(os.tmpdir(), `blindfold-seed-bundle-${process.pid}.json`);
    await fs.writeFile(bundlePath, JSON.stringify(bundle));

    const context = await browser.newContext({ baseURL: IMPORT_BUNDLE_BASE_URL });
    const page = await context.newPage();

    // Create the workspace with the checkbox left unticked — it lands empty.
    await page.goto("/ui/setup");
    await page.getByTestId("setup-workspace-name").fill("Acme Import");
    await page.getByTestId("setup-create-btn").click();
    await expect(page).toHaveURL(/\/ui\/entities$/);
    await expect(page.getByTestId("entity-list-empty-state")).toBeVisible();

    // Populate is NOT a first-run-only screen: navigate away to Setup and back —
    // the empty-state populate surface is still there, driven by the entity
    // count being zero, not by a "just created" flag.
    await page.goto("/ui/setup");
    await expect(page.locator("h1")).toContainText("Setup");
    await page.goto("/ui/entities");
    await expect(page.getByTestId("entity-list-empty-state")).toBeVisible();

    const requestHosts = new Set<string>();
    const createRequests: string[] = [];
    page.on("request", (req) => {
      requestHosts.add(new URL(req.url()).host);
      if (req.method() === "POST" && new URL(req.url()).pathname === "/v1/management/workspaces") {
        createRequests.push(req.url());
      }
    });

    await page.getByTestId("entity-list-import-bundle-input").setInputFiles(bundlePath);
    await expect(page.getByTestId("entity-list-empty-state")).toHaveCount(0);
    await expect(page.getByTestId("entity-table")).toBeVisible();

    // Importing from the entity list never creates a workspace of its own — it
    // targets THIS already-existing workspace only.
    expect(createRequests).toEqual([]);

    const firstPartyHost = new URL(IMPORT_BUNDLE_BASE_URL).host;
    const thirdParty = [...requestHosts].filter((host) => host !== firstPartyHost);
    expect(thirdParty, `unexpected non-loopback requests: ${thirdParty.join(", ")}`).toEqual([]);
    await context.close();
    await fs.unlink(bundlePath).catch(() => {});

    const api = await pwRequest.newContext({ baseURL: IMPORT_BUNDLE_BASE_URL });
    // The bundle's own `workspace` tag is ignored — entities land in the
    // already-created "acme-import" workspace the operator was viewing, not a
    // workspace derived from the bundle.
    const entitiesResp = await api.get("/v1/management/workspaces/acme-import/entities");
    const entities = (await entitiesResp.json()).entities as Array<{ active_surrogate: string }>;
    expect(entities).toHaveLength(2);
    expect(entities.every((e) => e.active_surrogate && e.active_surrogate !== "Priya Shah")).toBe(true);

    const ignoredResp = await api.get("/v1/management/workspaces/ignored-by-import/entities");
    const ignoredEntities = (await ignoredResp.json()).entities;
    expect(ignoredEntities).toEqual([]);

    // Privilege-escalation guard: the bundle's rbac_grants field must never have
    // been applied.
    const rolesResp = await api.get("/v1/management/workspaces/acme-import/roles", {
      headers: { "x-blindfold-identity": "" },
    });
    const roles = await rolesResp.json();
    expect(roles.assignments).not.toContainEqual(
      expect.objectContaining({ identity: "mallory" }),
    );
    await api.dispose();
  });
});
