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
});

test.describe("Setup — create first workspace", () => {
  test("creating a workspace grants the creator admin and lands on the populated app", async ({
    operatorPage,
  }) => {
    await operatorPage.goto("/ui/setup");
    await operatorPage.getByTestId("setup-workspace-name").fill("Acme Corp");
    await operatorPage.getByTestId("setup-create-btn").click();

    await expect(operatorPage).toHaveURL(/\/ui\/status$/);
    await expect(operatorPage.locator("h1")).toContainText("Status");

    // The SPA never sends x-blindfold-identity (issue #107's browser-side caller
    // is the default "" identity, ADR-0019's static single-owner model) — verify
    // the grant landed server-side through the real roles endpoint, exactly the
    // way an authorized admin would query it.
    const api = await pwRequest.newContext({ baseURL: EMPTY_BASE_URL });
    const rolesResp = await api.get("/v1/management/workspaces/acme-corp/roles", {
      headers: { "x-blindfold-identity": "" },
    });
    expect(rolesResp.status()).toBe(200);
    const body = await rolesResp.json();
    expect(body.assignments).toContainEqual({ identity: "", workspace: "acme-corp", role: "admin" });
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
