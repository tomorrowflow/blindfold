import { test as base, expect, request as pwRequest } from "@playwright/test";

// Setup shell (issue #107, Setup slice 4/5): runs against the THIRD serve_fixture.py
// instance (port 8953, BLINDFOLD_FIXTURE_STATE=empty, see playwright.config.ts) — a
// genuinely empty store, so the forced-redirect-to-/setup gate and the
// create-first-workspace/creator-becomes-admin flow exercise real state, not a stub.
//
// Tests run in declaration order (this project's `workers: 1`) because they share
// one running server: the redirect assertion needs the store still empty, the
// create assertion then populates it, and the final "no longer forced" assertion
// needs the now-populated store.

const EMPTY_BASE_URL = "http://127.0.0.1:8953";

const test = base.extend<{ operatorPage: import("@playwright/test").Page }>({
  operatorPage: async ({ browser }, use) => {
    const context = await browser.newContext({ baseURL: EMPTY_BASE_URL });
    const page = await context.newPage();
    await use(page);
    await context.close();
  },
});

test.describe("Setup — empty-store forced redirect", () => {
  test("an empty store redirects a management route to /ui/setup", async ({ operatorPage }) => {
    await operatorPage.goto("/ui/entities");
    await expect(operatorPage).toHaveURL(/\/ui\/setup$/);
    await expect(operatorPage.locator("h1")).toContainText("Setup");
  });

  test("the redirect makes zero requests to a non-loopback origin", async ({ operatorPage }) => {
    const requestHosts = new Set<string>();
    operatorPage.on("request", (req) => requestHosts.add(new URL(req.url()).host));

    await operatorPage.goto("/ui/status");
    await expect(operatorPage).toHaveURL(/\/ui\/setup$/);

    const firstPartyHost = new URL(EMPTY_BASE_URL).host;
    const thirdParty = [...requestHosts].filter((host) => host !== firstPartyHost);
    expect(thirdParty, `unexpected non-loopback requests: ${thirdParty.join(", ")}`).toEqual([]);
  });

  test("the Enhanced local detection toggle is hidden on the in-memory default store (ADR-0034 §2, issue #146)", async ({
    operatorPage,
  }) => {
    // This fixture never sets BLINDFOLD_DATABASE_URL, so /v1/status's
    // config.has_persistent_store is false -- restart-to-activate is incoherent
    // on the ephemeral in-memory default, so the toggle must not even be offered.
    await operatorPage.goto("/ui/setup");
    await expect(operatorPage.getByTestId("setup-workspace-name")).toBeVisible();
    await expect(operatorPage.getByTestId("setup-gliner-checkbox")).toHaveCount(0);
  });

  test("the ephemeral-store honesty banner shows on the in-memory default store (issue #199, ADR-0043)", async ({
    operatorPage,
  }) => {
    // Same has_persistent_store=false signal as the toggle-hidden case above --
    // an unset BLINDFOLD_DATABASE_URL means every workspace/entity is lost on
    // restart, so Setup must say so rather than stay silent.
    await operatorPage.goto("/ui/setup");
    const banner = operatorPage.getByTestId("setup-ephemeral-store-banner");
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("opted out of persistence");
    await expect(banner).toContainText("BLINDFOLD_DATABASE_URL");
    // The unencrypted-persistence banner (ADR-0045 §4/§10, issue #227) is a
    // distinct condition (a persistent store configured with no mapping cipher)
    // -- on a store with no persistence at all, it must not also render. See
    // setup-unencrypted-store.spec.ts for the persistent-store fixture where
    // this banner's own show/hide behavior is exercised.
    await expect(
      operatorPage.getByTestId("setup-unencrypted-store-banner")
    ).toHaveCount(0);
  });
});

test.describe("Setup — create first workspace", () => {
  test("creating a workspace (Load sample data left unticked) grants the creator every canonical role and, once Setup's ready-to-connect step is skipped, lands in that workspace's empty entity list", async ({
    operatorPage,
  }) => {
    await operatorPage.goto("/ui/setup");
    await operatorPage.getByTestId("setup-workspace-name").fill("Acme Corp");
    await operatorPage.getByTestId("setup-create-btn").click();

    // Issue #264: creating a workspace no longer redirects straight to the entity
    // list -- it lands on the ready-to-connect terminal step first (see the
    // "Setup — ready to connect" describe block below for its own two CTAs).
    // Skip it here since this test's own concern is the founding-grant + landing
    // page, not the new step itself.
    await expect(operatorPage.getByTestId("setup-ready-message")).toBeVisible();
    await operatorPage.getByTestId("setup-skip-connect").click();

    await expect(operatorPage).toHaveURL(/\/ui\/entities$/);
    await expect(operatorPage.locator("h1")).toContainText("Entity list");
    // Create and populate are decoupled (ADR-0030): the checkbox was left
    // unticked, so the workspace lands empty, offering the persistent
    // Import/Sample-data populate surface rather than a populated table.
    await expect(operatorPage.getByTestId("entity-list-empty-state")).toBeVisible();

    // The SPA never sends x-blindfold-identity (issue #107's browser-side caller
    // is the default "" identity, ADR-0019's static single-owner model) — verify
    // the founding grant landed server-side through the real roles endpoint,
    // exactly the way an authorized admin would query it. Issue #156: the founding
    // identity gets every canonical role (viewer/curator/re-identifier/admin), not
    // just admin, so it isn't locked out of the viewer-gated views.
    const api = await pwRequest.newContext({ baseURL: EMPTY_BASE_URL });
    const rolesResp = await api.get("/v1/management/workspaces/acme-corp/roles", {
      headers: { "x-blindfold-identity": "" },
    });
    expect(rolesResp.status()).toBe(200);
    const body = await rolesResp.json();
    for (const role of ["viewer", "curator", "re-identifier", "admin"]) {
      expect(body.assignments).toContainEqual({ identity: "", workspace: "acme-corp", role });
    }
    await api.dispose();
  });

  test("once a workspace exists, a management route no longer redirects to Setup", async ({
    operatorPage,
  }) => {
    await operatorPage.goto("/ui/entities");
    await expect(operatorPage).toHaveURL(/\/ui\/entities$/);
    await expect(operatorPage.locator("nav.bf-sidebar")).toBeVisible();
  });

  test("Setup itself stays reachable once a workspace exists", async ({ operatorPage }) => {
    await operatorPage.goto("/ui/setup");
    await expect(operatorPage).toHaveURL(/\/ui\/setup$/);
    await expect(operatorPage.locator("h1")).toContainText("Setup");
  });
});

test.describe("Setup — ready to connect (issue #264)", () => {
  // readyToConnect is client-side-only React state (reset on every fresh mount of
  // Setup), so each test below creates its own new workspace to reach the screen
  // again -- creating an additional workspace on an already-non-empty store is
  // ungated (POST /v1/management/workspaces docstring: no role check, it just
  // skips the founding-admin self-grant), so this is safe to repeat.

  test("the ready-to-connect screen shows both CTAs and doesn't auto-navigate", async ({
    operatorPage,
  }) => {
    await operatorPage.goto("/ui/setup");
    await operatorPage.getByTestId("setup-workspace-name").fill("Umbrella Corp");
    await operatorPage.getByTestId("setup-create-btn").click();

    await expect(operatorPage.getByTestId("setup-ready-message")).toHaveText(
      "You're set up — now point a tool at it."
    );
    await expect(operatorPage).toHaveURL(/\/ui\/setup$/);
    await expect(operatorPage.getByTestId("setup-connect-cta")).toHaveAttribute(
      "href",
      "/ui/connect"
    );
    await expect(operatorPage.getByTestId("setup-skip-connect")).toBeVisible();
  });

  test("the Connect a tool CTA navigates to /connect", async ({ operatorPage }) => {
    await operatorPage.goto("/ui/setup");
    await operatorPage.getByTestId("setup-workspace-name").fill("Stark Industries");
    await operatorPage.getByTestId("setup-create-btn").click();
    await operatorPage.getByTestId("setup-connect-cta").click();
    await expect(operatorPage).toHaveURL(/\/ui\/connect$/);
  });

  test("the Skip for now CTA navigates to /entities", async ({ operatorPage }) => {
    await operatorPage.goto("/ui/setup");
    await operatorPage.getByTestId("setup-workspace-name").fill("Wayne Enterprises");
    await operatorPage.getByTestId("setup-create-btn").click();
    await operatorPage.getByTestId("setup-skip-connect").click();
    await expect(operatorPage).toHaveURL(/\/ui\/entities$/);
  });
});
