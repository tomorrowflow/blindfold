import { test, expect } from "@playwright/test";

// Access phantom-identity-row bug (issue #177). Runs against its OWN genuinely-
// empty serve_fixture.py instance (port 8956, BLINDFOLD_FIXTURE_STATE=empty, see
// playwright.config.ts) -- the SPA never sends x-blindfold-identity (ADR-0019's
// static single-owner model), so creating the first workspace self-grants every
// canonical role to the anonymous "" caller (issue #156/#107). That "" identity
// is a real RBAC grant (kept as-is at the API layer, asserted by
// setup-shell.spec.ts), but it is not a genuine, human-readable identity -- the
// Access view must not render it as a row with a blank avatar and an empty name.

const BASE_URL = "http://127.0.0.1:8956";

test.describe("access shell — anonymous founding grant renders no phantom row", () => {
  test("a workspace whose only grantee is the anonymous \"\" caller shows zero identity rows, not a blank one", async ({
    browser,
  }) => {
    const context = await browser.newContext({ baseURL: BASE_URL });
    const page = await context.newPage();

    await page.goto("/ui/setup");
    await page.getByTestId("setup-workspace-name").fill("Acme Corp");
    await page.getByTestId("setup-create-btn").click();
    await expect(page).toHaveURL(/\/ui\/entities$/);

    await page.goto("/ui/access");
    await expect(page.locator("h1")).toHaveText("Access");
    // The anonymous "" caller already holds admin on this workspace (that's the
    // whole founding-grant mechanism), so the editor renders rather than the
    // locked state.
    await expect(page.getByTestId("access-locked")).toHaveCount(0);

    // "" is not a genuine identity -- no row for it, blank avatar or otherwise.
    await expect(page.getByTestId("access-row-")).toHaveCount(0);
    await expect(page.locator(".bf-access-table tbody tr")).toHaveCount(0);

    await context.close();
  });
});
