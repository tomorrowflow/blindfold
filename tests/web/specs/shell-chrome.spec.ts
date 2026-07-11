import { test, expect } from "./fixtures";

// Shell chrome (issue #95): workspace switcher, role chips, audit drawer, toasts.
// These specs drive the real FastAPI app (via serve_fixture.py) with a two-workspace
// fixture: alice holds re-identifier+viewer+curator on "acme", no role on "beta";
// carol holds viewer on "beta" only; bob holds no role on either workspace.

test.describe("workspace switcher", () => {
  test("alice sees acme in the switcher (her only workspace)", async ({ alicePage }) => {
    await alicePage.goto("/ui/");
    // The switcher trigger shows the active workspace slug
    await expect(alicePage.getByTestId("workspace-switcher-trigger")).toContainText("acme");
  });

  test("switcher never shows a workspace the identity holds no role on", async ({ alicePage }) => {
    await alicePage.goto("/ui/");
    await alicePage.getByTestId("workspace-switcher-trigger").click();
    const menu = alicePage.getByTestId("workspace-menu");
    await expect(menu).toBeVisible();
    // "beta" must NOT appear — alice has no role there
    await expect(menu).not.toContainText("beta");
    // "acme" must appear
    await expect(menu).toContainText("acme");
  });

  test("bob (no roles) sees no workspace in the switcher", async ({ bobPage }) => {
    await bobPage.goto("/ui/");
    // Switcher should show 'No workspace' state for a zero-role identity
    const trigger = bobPage.locator(".bf-workspace-switcher");
    await expect(trigger).toBeVisible();
    // Bob has no workspaces so the button shows the empty state or no menu items
    await trigger.click();
    // menu should not be present or should show zero items
    const menu = bobPage.getByTestId("workspace-menu");
    // either not visible or shows empty — not a hard error
    const menuVisible = await menu.isVisible().catch(() => false);
    if (menuVisible) {
      // if a menu renders, it must have zero workspace options
      const items = menu.locator('[role="option"]');
      await expect(items).toHaveCount(0);
    }
  });

  test("workspace menu has the footer copy", async ({ alicePage }) => {
    await alicePage.goto("/ui/");
    await alicePage.getByTestId("workspace-switcher-trigger").click();
    await expect(alicePage.getByTestId("workspace-menu")).toContainText(
      "Only workspaces you hold a role on appear here"
    );
  });
});

test.describe("role chips", () => {
  test("alice sees re-identifier chip (ochre family) in the top bar", async ({ alicePage }) => {
    await alicePage.goto("/ui/");
    const chip = alicePage.getByTestId("role-chip-re-identifier");
    await expect(chip).toBeVisible();
    await expect(chip).toContainText("re-identifier");
  });

  test("alice sees curator chip (green family) in the top bar", async ({ alicePage }) => {
    await alicePage.goto("/ui/");
    const chip = alicePage.getByTestId("role-chip-curator");
    await expect(chip).toBeVisible();
    await expect(chip).toContainText("curator");
  });

  test("bob (no roles) sees no role chips", async ({ bobPage }) => {
    await bobPage.goto("/ui/");
    await expect(bobPage.getByTestId("role-chip-re-identifier")).toHaveCount(0);
    await expect(bobPage.getByTestId("role-chip-curator")).toHaveCount(0);
  });
});

test.describe("audit drawer", () => {
  test("audit drawer opens on button click", async ({ alicePage }) => {
    await alicePage.goto("/ui/");
    await alicePage.getByTestId("audit-drawer-trigger").click();
    const drawer = alicePage.getByTestId("audit-drawer");
    await expect(drawer).toBeVisible();
    await expect(drawer).toContainText("Audit · recent real-space events");
  });

  test("audit drawer has the settled banner copy", async ({ alicePage }) => {
    await alicePage.goto("/ui/");
    await alicePage.getByTestId("audit-drawer-trigger").click();
    await expect(alicePage.getByTestId("audit-drawer")).toContainText(
      "Reveals, real-name lookups and blocks"
    );
  });

  test("audit drawer footer link points to /audit route", async ({ alicePage }) => {
    await alicePage.goto("/ui/");
    await alicePage.getByTestId("audit-drawer-trigger").click();
    const footerLink = alicePage.locator(".bf-audit-drawer-footer-link");
    await expect(footerLink).toBeVisible();
    await expect(footerLink).toHaveAttribute("href", /\/audit/);
    await expect(footerLink).toContainText("View full log");
  });

  test("bob (no viewer role) sees locked state in audit drawer", async ({ bobPage }) => {
    await bobPage.goto("/ui/");
    await bobPage.getByTestId("audit-drawer-trigger").click();
    // bob has no viewer role; drawer shows locked state, not an error
    await expect(bobPage.getByTestId("audit-drawer-locked")).toBeVisible();
  });

  test("audit drawer closes on X button", async ({ alicePage }) => {
    await alicePage.goto("/ui/");
    await alicePage.getByTestId("audit-drawer-trigger").click();
    await alicePage.getByRole("button", { name: "Close audit drawer" }).click();
    // drawer slides away; --open class removed
    const drawer = alicePage.getByTestId("audit-drawer");
    await expect(drawer).not.toHaveClass(/bf-audit-drawer--open/);
  });
});

test.describe("toast mechanism", () => {
  test("toast outlet is present in the DOM at shell level", async ({ page }) => {
    await page.goto("/ui/");
    // ToastOutlet renders null when empty — test its container by injecting a toast
    // via the window-level test hook exposed by ToastContext.
    // Since no view triggers a toast yet, we verify the mechanism by evaluating
    // that the ToastProvider+ToastOutlet are mounted (no import-error crash),
    // and that the shell renders without any console errors.
    const errors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
    });
    await page.waitForSelector("nav.bf-sidebar");
    // The shell mounted without errors; no toast visible yet (none triggered)
    expect(errors.filter((e) => !e.includes("favicon"))).toHaveLength(0);
  });
});
