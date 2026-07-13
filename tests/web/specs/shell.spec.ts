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
    // Scoped to the sidebar landmark, not the whole page: issue #110's Home/Status
    // review-inbox rail card also renders a "Review inbox" link (its own "Open
    // review inbox" copy), so an unscoped page-wide locator is ambiguous.
    const sidebar = page.getByRole("navigation", { name: "Management navigation" });

    for (const label of NAV_LABELS) {
      await expect(sidebar.getByRole("link", { name: label })).toBeVisible();
    }

    await expect(sidebar.getByRole("link", { name: "Home" })).toHaveAttribute(
      "aria-current",
      "page"
    );

    await sidebar.getByRole("link", { name: "Entity list" }).click();
    await expect(page).toHaveURL(/\/ui\/entities$/);
    await expect(sidebar.getByRole("link", { name: "Entity list" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    await expect(sidebar.getByRole("link", { name: "Home" })).not.toHaveAttribute(
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

  test("deep link to a sidebar route renders directly", async ({ page }) => {
    await page.goto("/ui/audit");
    await expect(page.getByRole("heading", { name: "Audit log" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Audit log" })).toHaveAttribute(
      "aria-current",
      "page"
    );
  });
});
