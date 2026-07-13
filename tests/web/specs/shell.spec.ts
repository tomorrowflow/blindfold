import { test, expect } from "./fixtures";

// Management SPA shell (ADR-0026, issue #93): smoke-checks the vendored Vite+React
// bundle actually renders in a real browser — sidebar destinations, collapse/expand,
// and client-side route switching. Fonts/icons come from the bundle (asserted
// separately by shell-egress-hygiene.spec.ts).

const NAV_LABELS = [
  "Home",
  "Entity list",
  "Graph editor",
  "Review inbox",
  "Audit log",
  "Access",
  "Settings",
];

test.describe("management shell", () => {
  test("sidebar renders all seven destinations and routes switch on click", async ({ page }) => {
    await page.goto("/ui/");

    for (const label of NAV_LABELS) {
      await expect(page.getByRole("link", { name: label })).toBeVisible();
    }

    await expect(page.getByRole("link", { name: "Home" })).toHaveAttribute(
      "aria-current",
      "page"
    );

    await page.getByRole("link", { name: "Entity list" }).click();
    await expect(page).toHaveURL(/\/ui\/entities$/);
    await expect(page.getByRole("link", { name: "Entity list" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    await expect(page.getByRole("link", { name: "Home" })).not.toHaveAttribute(
      "aria-current",
      "page"
    );
    await expect(page.getByRole("heading", { name: "Entity list" })).toBeVisible();
  });

  test("sidebar collapses and expands", async ({ page }) => {
    await page.goto("/ui/");

    const sidebar = page.locator("nav.bf-sidebar");
    await expect(sidebar).toHaveAttribute("data-collapsed", "false");
    await expect(page.getByRole("link", { name: "Home" })).toBeVisible();

    await page.getByRole("button", { name: "Collapse sidebar" }).click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "true");

    await page.getByRole("button", { name: "Expand sidebar" }).click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "false");
  });

  test("sidebar shows the brand block: glyph, 'Blindfold', and a 'Management' subline", async ({
    page,
  }) => {
    await page.goto("/ui/");
    const brand = page.locator(".bf-sidebar-brand");
    await expect(brand).toBeVisible();
    await expect(brand.locator(".bf-sidebar-brand-glyph")).toBeVisible();
    await expect(brand).toContainText("Blindfold");
    await expect(brand).toContainText("Management");
  });

  test("collapse control sits at the sidebar bottom, below the nav; collapsed sidebar is an icon rail", async ({
    page,
  }) => {
    await page.goto("/ui/");
    const sidebar = page.locator("nav.bf-sidebar");
    const order = await sidebar.locator("> *").evaluateAll((els) =>
      els.map((el) => el.className)
    );
    const brandIdx = order.findIndex((c) => c.includes("bf-sidebar-brand"));
    const navIdx = order.findIndex((c) => c.includes("bf-nav"));
    const toggleIdx = order.findIndex((c) => c.includes("bf-sidebar-toggle"));
    expect(brandIdx).toBeLessThan(navIdx);
    expect(toggleIdx).toBeGreaterThan(navIdx);

    await page.getByRole("button", { name: "Collapse sidebar" }).click();
    await expect(sidebar).toHaveAttribute("data-collapsed", "true");
    // Icon rail: nav labels and the brand wordmark text hide, the glyph stays.
    await expect(page.locator(".bf-sidebar-brand-glyph")).toBeVisible();
    await expect(page.locator(".bf-nav-label")).toHaveCount(0);
  });

  test("deep link to a sidebar route renders directly", async ({ page }) => {
    await page.goto("/ui/audit");
    await expect(page.getByRole("heading", { name: "Audit log" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Audit log" })).toHaveAttribute(
      "aria-current",
      "page"
    );
  });
});
