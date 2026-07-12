import { test, expect } from "./fixtures";

// Access view (issue #103): workspace RBAC admin — list/grant/revoke the ADR-0028
// roles {viewer, curator, re-identifier, admin}, admin-gated. Replaces the /access
// StubView (issue #93 scope). Backing API: /v1/management/workspaces/{slug}/roles
// (admin-gated list/grant/revoke — already shipped and covered by
// tests/test_audit_viewer_rbac.py). This spec drives the real /ui/access shell route.
//
// Fixture roles (serve_fixture.py): alice holds admin (+ viewer/curator/re-identifier)
// on WORKSPACE ("acme"); dave holds ONLY curator (no admin); bob holds no role anywhere.

test.describe("access shell — admin renders the editor", () => {
  test("admin sees the Access header, subtitle and Add identity button", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/access");
    await expect(alicePage.locator("h1")).toHaveText("Access");
    await expect(alicePage.locator("body")).toContainText("acme");
    await expect(alicePage.getByTestId("add-identity-btn")).toBeVisible();
  });
});

test.describe("access shell — identity table", () => {
  test("lists identities with their current roles as chips", async ({ alicePage }) => {
    await alicePage.goto("/ui/access");
    const aliceRow = alicePage.getByTestId("access-row-alice");
    await expect(aliceRow).toBeVisible();
    await expect(aliceRow.getByTestId("role-chip-admin")).toBeVisible();
    await expect(aliceRow.getByTestId("role-chip-curator")).toBeVisible();
    await expect(aliceRow.getByTestId("role-chip-re-identifier")).toBeVisible();
    await expect(aliceRow.getByTestId("role-chip-viewer")).toBeVisible();

    const daveRow = alicePage.getByTestId("access-row-dave");
    await expect(daveRow).toBeVisible();
    await expect(daveRow.getByTestId("role-chip-curator")).toBeVisible();
    await expect(daveRow.getByTestId("role-chip-admin")).toHaveCount(0);
  });
});

test.describe("access shell — grant/revoke round trip", () => {
  // Exercises dave's `viewer` role only, never alice's — alice's admin/curator/
  // re-identifier/viewer grants are load-bearing fixture state for every OTHER spec
  // file in this suite (audit queries, structural edits, reveal). The round trip
  // ends with dave back at his original curator-only state, so later spec files
  // (alphabetically after this one) see the fixture unperturbed.
  test("grant offers only missing roles; revoke re-offers the grant button", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/access");
    const daveRow = alicePage.getByTestId("access-row-dave");

    await expect(daveRow.getByTestId("role-chip-viewer")).toHaveCount(0);
    await daveRow.getByTestId("grant-btn-dave-viewer").click();
    await expect(daveRow.getByTestId("role-chip-viewer")).toBeVisible();
    await expect(daveRow.getByTestId("grant-btn-dave-viewer")).toHaveCount(0);

    await daveRow.getByTestId("revoke-btn-dave-viewer").click();
    await expect(daveRow.getByTestId("role-chip-viewer")).toHaveCount(0);
    await expect(daveRow.getByTestId("grant-btn-dave-viewer")).toBeVisible();
  });
});

test.describe("access shell — add identity", () => {
  test("typing an identity and a role grants that identity its first role", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/access");
    await expect(alicePage.getByTestId("access-row-erin")).toHaveCount(0);

    await alicePage.getByTestId("add-identity-btn").click();
    await alicePage.getByTestId("add-identity-input").fill("erin");
    await alicePage.getByTestId("add-identity-role-select").selectOption("viewer");
    await alicePage.getByTestId("add-identity-submit").click();

    const erinRow = alicePage.getByTestId("access-row-erin");
    await expect(erinRow).toBeVisible();
    await expect(erinRow.getByTestId("role-chip-viewer")).toBeVisible();
  });
});

test.describe("access shell — non-admin gets a locked state", () => {
  test("a curator without admin sees a locked state, not the editor", async ({
    davePage,
  }) => {
    await davePage.goto("/ui/access");
    await expect(davePage.getByTestId("access-locked")).toBeVisible();
    await expect(davePage.getByTestId("add-identity-btn")).toHaveCount(0);
  });
});

test.describe("access shell — egress hygiene", () => {
  test("grant/revoke round trip issues zero non-loopback requests", async ({
    alicePage,
    baseURL,
  }) => {
    const hosts = new Set<string>();
    alicePage.on("request", (req) => hosts.add(new URL(req.url()).host));

    await alicePage.goto("/ui/access");
    const daveRow = alicePage.getByTestId("access-row-dave");
    await daveRow.getByTestId("grant-btn-dave-viewer").click();
    await expect(daveRow.getByTestId("role-chip-viewer")).toBeVisible();
    await daveRow.getByTestId("revoke-btn-dave-viewer").click();
    await expect(daveRow.getByTestId("role-chip-viewer")).toHaveCount(0);

    const firstPartyHost = new URL(baseURL!).host;
    const thirdParty = [...hosts].filter((host) => host !== firstPartyHost);
    expect(thirdParty, `unexpected non-loopback requests: ${thirdParty.join(", ")}`).toEqual([]);
  });
});

test.describe("access shell — nav gating", () => {
  test("non-admin sees the Access nav item disabled and it does not navigate", async ({
    davePage,
  }) => {
    await davePage.goto("/ui/entities");
    const accessLink = davePage.getByRole("link", { name: "Access" });
    await expect(accessLink).toHaveAttribute("aria-disabled", "true");
    await accessLink.click({ force: true });
    await expect(davePage).toHaveURL(/\/ui\/entities$/);
  });

  test("admin sees the Access nav item enabled and navigating works", async ({ alicePage }) => {
    await alicePage.goto("/ui/entities");
    const accessLink = alicePage.getByRole("link", { name: "Access" });
    await expect(accessLink).not.toHaveAttribute("aria-disabled", "true");
    await accessLink.click();
    await expect(alicePage).toHaveURL(/\/ui\/access$/);
  });
});
