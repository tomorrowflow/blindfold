import { test, expect, WORKSPACE } from "./fixtures";

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

test.describe("access shell — identity cell (issue #115)", () => {
  test("identity row shows derived initials in the avatar and the identity string in mono", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/access");
    const daveRow = alicePage.getByTestId("access-row-dave");
    await expect(daveRow.locator(".bf-access-avatar")).toHaveText("DA");
    const identityName = daveRow.locator(".bf-access-identity-name");
    await expect(identityName).toHaveText("dave");
    await expect(identityName).toHaveCSS("font-family", /IBM Plex Mono/);
  });
});

test.describe("access shell — re-identifier ochre / curator green (issue #115)", () => {
  test("re-identifier and curator chips use their reserved colors; admin does not", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/access");
    const aliceRow = alicePage.getByTestId("access-row-alice");

    await expect(aliceRow.getByTestId("role-chip-re-identifier")).toHaveCSS(
      "color",
      "rgb(176, 127, 32)" // --bf-ochre
    );
    await expect(aliceRow.getByTestId("role-chip-curator")).toHaveCSS(
      "color",
      "rgb(35, 122, 82)" // --bf-curator
    );
    await expect(aliceRow.getByTestId("role-chip-admin")).not.toHaveCSS(
      "color",
      "rgb(176, 127, 32)"
    );
  });

  test("re-identifier and curator grant buttons use their reserved colors; admin does not", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/access");
    // Seed a throwaway identity (frank) holding only viewer, so curator/re-identifier/
    // admin all still render as grant buttons. Revoked at the end to leave the shared
    // fixture unperturbed for later specs in this file (mirrors the dave round-trip's
    // own restore-at-the-end discipline above).
    await alicePage.getByTestId("add-identity-btn").click();
    await alicePage.getByTestId("add-identity-input").fill("frank");
    await alicePage.getByTestId("add-identity-role-select").selectOption("viewer");
    await alicePage.getByTestId("add-identity-submit").click();

    const frankRow = alicePage.getByTestId("access-row-frank");
    await expect(frankRow.getByTestId("grant-btn-frank-re-identifier")).toHaveCSS(
      "color",
      "rgb(176, 127, 32)" // --bf-ochre
    );
    await expect(frankRow.getByTestId("grant-btn-frank-curator")).toHaveCSS(
      "color",
      "rgb(35, 122, 82)" // --bf-curator
    );
    await expect(frankRow.getByTestId("grant-btn-frank-admin")).not.toHaveCSS(
      "color",
      "rgb(176, 127, 32)"
    );

    await frankRow.getByTestId("revoke-btn-frank-viewer").click();
    await expect(alicePage.getByTestId("access-row-frank")).toHaveCount(0);
  });
});

test.describe("access shell — role glossary (issue #125)", () => {
  test("each role chip and grant button surfaces the ADR-0028 one-line meaning", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/access");
    const aliceRow = alicePage.getByTestId("access-row-alice");

    await expect(aliceRow.getByTestId("role-chip-curator")).toHaveAttribute(
      "title",
      /never unmask/
    );
    await expect(aliceRow.getByTestId("role-chip-re-identifier")).toHaveAttribute(
      "title",
      /every attempt audited/
    );

    const daveRow = alicePage.getByTestId("access-row-dave");
    await expect(daveRow.getByTestId("grant-btn-dave-viewer")).toHaveAttribute(
      "title",
      /read audit events/
    );
    await expect(daveRow.getByTestId("grant-btn-dave-admin")).toHaveAttribute(
      "title",
      /grant\/revoke roles/
    );
  });
});

test.describe("access shell — admin self-revoke lockout guard (issue #125)", () => {
  test("revoking your own last admin role warns before commit; cancel leaves it granted", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/access");
    const aliceRow = alicePage.getByTestId("access-row-alice");
    await expect(aliceRow.getByTestId("role-chip-admin")).toBeVisible();

    // alice is the sole admin on "acme" (serve_fixture.py) — revoking her own
    // admin role here would lock every identity out of workspace administration.
    await aliceRow.getByTestId("revoke-btn-alice-admin").click();

    const warning = alicePage.getByTestId("admin-lockout-warning");
    await expect(warning).toBeVisible();
    await expect(warning).toContainText(/only admin/i);

    // Not yet committed — the chip is still there and no revoke has happened.
    await expect(aliceRow.getByTestId("role-chip-admin")).toBeVisible();

    await alicePage.getByTestId("admin-lockout-cancel").click();
    await expect(alicePage.getByTestId("admin-lockout-warning")).toHaveCount(0);
    await expect(aliceRow.getByTestId("role-chip-admin")).toBeVisible();
  });

  test("still warns (with different copy) when another identity also holds admin, and confirming commits the revoke and locks the view", async ({
    alicePage,
    request,
  }) => {
    await alicePage.goto("/ui/access");

    // Give "grace" admin too. Roles are flat (ADR-0028): revoking your own
    // admin role always ends *your own* session's access to this view, even
    // when the workspace itself still has another admin — so the guard must
    // still warn here, just with copy that doesn't claim a full lockout.
    await alicePage.getByTestId("add-identity-btn").click();
    await alicePage.getByTestId("add-identity-input").fill("grace");
    await alicePage.getByTestId("add-identity-role-select").selectOption("admin");
    await alicePage.getByTestId("add-identity-submit").click();
    await expect(alicePage.getByTestId("access-row-grace")).toBeVisible();

    const aliceRow = alicePage.getByTestId("access-row-alice");
    await aliceRow.getByTestId("revoke-btn-alice-admin").click();

    const warning = alicePage.getByTestId("admin-lockout-warning");
    await expect(warning).toBeVisible();
    await expect(warning).not.toContainText(/only admin/i);
    await expect(warning).toContainText(/your access/i);

    await alicePage.getByTestId("admin-lockout-confirm").click();

    // Committed: alice's own session immediately sees the locked view, even
    // though grace still holds admin on the workspace.
    await expect(alicePage.getByTestId("access-locked")).toBeVisible();

    // Restore fixture state for every later spec in this file: re-grant alice
    // admin and drop grace entirely. Alice's own browser session can no longer
    // call an admin-gated endpoint (she just lost admin), so this goes through
    // the API directly as "grace" — the only remaining admin — not the UI.
    await request.post(`/v1/management/workspaces/${WORKSPACE}/roles`, {
      headers: { "x-blindfold-identity": "grace", "content-type": "application/json" },
      data: { identity: "alice", role: "admin" },
    });
    await request.delete(`/v1/management/workspaces/${WORKSPACE}/roles/grace?role=admin`, {
      headers: { "x-blindfold-identity": "grace" },
    });

    await alicePage.reload();
    await expect(
      alicePage.getByTestId("access-row-alice").getByTestId("role-chip-admin")
    ).toBeVisible();
    await expect(alicePage.getByTestId("access-row-grace")).toHaveCount(0);
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
