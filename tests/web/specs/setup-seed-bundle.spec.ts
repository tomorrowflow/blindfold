import { promises as fs } from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { test, expect, request as pwRequest } from "@playwright/test";

// Seed bundle import + one-click Sample data (issue #108, Setup slice 5/5): each
// describe block below runs against its OWN genuinely-empty serve_fixture.py
// instance (ports 8954/8955, BLINDFOLD_FIXTURE_STATE=empty, see
// playwright.config.ts) — "Load sample data" and "Import a Seed bundle" each
// self-grant admin on their own new workspace only when the store was empty
// beforehand (issue #107's privilege-escalation guard), so each needs a store of
// its own, independent of setup-shell.spec.ts's own empty-store instance too.
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

test.describe("Setup — one-click Sample data", () => {
  test("loading Sample data auto-creates `default`, grants admin, and populates the entity graph", async ({
    browser,
  }) => {
    const context = await browser.newContext({ baseURL: SAMPLE_DATA_BASE_URL });
    const page = await context.newPage();
    const requestHosts = new Set<string>();
    page.on("request", (req) => requestHosts.add(new URL(req.url()).host));

    await page.goto("/ui/setup");
    await page.getByTestId("setup-sample-data-btn").click();

    await expect(page).toHaveURL(/\/ui\/status$/);
    await expect(page.locator("h1")).toContainText("Status");

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
    const rolesResp = await api.get("/v1/management/workspaces/default/roles", {
      headers: { "x-blindfold-identity": "" },
    });
    expect(rolesResp.status()).toBe(200);
    const roles = await rolesResp.json();
    expect(roles.assignments).toContainEqual({ identity: "", workspace: "default", role: "admin" });

    // The vendored bundle's 5 persons + 3 terms landed in the entity graph, in
    // surrogate space only (never a real name) — the same GET the entity-list
    // shell view uses.
    const entitiesResp = await api.get("/v1/management/workspaces/default/entities");
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

test.describe("Setup — Import a Seed bundle", () => {
  test("importing a bundle creates its own workspace and mints local surrogates, never RBAC grants", async ({
    browser,
  }) => {
    const bundle = {
      workspace: { slug: "acme-import", name: "Acme Import Co" },
      persons: [{ canonical_name: "Priya Shah", variations: ["Priya"] }],
      terms: [{ canonical_name: "Zenith Robotics", variations: [] }],
      // Not part of the ADR-0029 dictionary-only contract -- must be ignored.
      rbac_grants: [{ identity: "mallory", workspace: "acme-import", role: "admin" }],
    };
    const bundlePath = path.join(os.tmpdir(), `blindfold-seed-bundle-${process.pid}.json`);
    await fs.writeFile(bundlePath, JSON.stringify(bundle));

    const context = await browser.newContext({ baseURL: IMPORT_BUNDLE_BASE_URL });
    const page = await context.newPage();
    const requestHosts = new Set<string>();
    page.on("request", (req) => requestHosts.add(new URL(req.url()).host));

    await page.goto("/ui/setup");
    await page.getByTestId("setup-import-bundle-input").setInputFiles(bundlePath);

    await expect(page).toHaveURL(/\/ui\/status$/);

    const firstPartyHost = new URL(IMPORT_BUNDLE_BASE_URL).host;
    const thirdParty = [...requestHosts].filter((host) => host !== firstPartyHost);
    expect(thirdParty, `unexpected non-loopback requests: ${thirdParty.join(", ")}`).toEqual([]);
    await context.close();
    await fs.unlink(bundlePath).catch(() => {});

    const api = await pwRequest.newContext({ baseURL: IMPORT_BUNDLE_BASE_URL });
    const entitiesResp = await api.get("/v1/management/workspaces/acme-import/entities");
    const entities = (await entitiesResp.json()).entities as Array<{ active_surrogate: string }>;
    expect(entities).toHaveLength(2);
    expect(entities.every((e) => e.active_surrogate && e.active_surrogate !== "Priya Shah")).toBe(true);

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
