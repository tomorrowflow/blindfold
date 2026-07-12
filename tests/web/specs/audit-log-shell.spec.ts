import { test, expect } from "./fixtures";

// Audit log view (issue #102): the full-page counterpart to the top-bar audit
// drawer (issue #95) — GET /v1/management/audit, viewer-gated, scrubbed rows.
// serve_fixture.py seeds one reveal (alice), one lookup (alice), one denied
// reveal (dave), one block (no actor), and one old lookup (2020, outside the
// default "Last 7 days" window) — see its own seeding comment for the exact set.

test.describe("Audit log — alice (holds viewer)", () => {
  test("renders header, subtitle and the five-column table", async ({ alicePage }) => {
    await alicePage.goto("/ui/audit");
    const view = alicePage.getByTestId("audit-log-page");
    await expect(view.locator("h1")).toHaveText("Audit log");
    await expect(view).toContainText("Structural edits are never logged");
    const headers = alicePage.locator("[data-testid='audit-log-table'] th");
    await expect(headers).toHaveText(["Time", "Kind", "Workspace", "Actor", "Detail"]);
  });

  test("shows the recent reveal, lookup, denied-reveal and block rows by default (7-day window)", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/audit");
    // 4 of the 5 seeded records fall inside the default window; the 2020 lookup does not.
    const rows = alicePage.getByTestId("audit-log-row");
    await expect(rows).toHaveCount(4);
    await expect(alicePage.getByTestId("audit-log-table")).toContainText("alice");
    await expect(alicePage.getByTestId("audit-log-table")).toContainText("dave");
  });

  test("kind pills use the reserved ochre family for reveal/lookup and red for block", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/audit");
    const pills = alicePage.getByTestId("audit-log-row-kind");
    const kinds = await pills.evaluateAll((els) => els.map((el) => el.getAttribute("data-kind")));
    expect(new Set(kinds)).toEqual(new Set(["reveal", "lookup", "block"]));

    const revealPill = pills.filter({ hasText: "Reveal" }).first();
    await expect(revealPill).toHaveCSS("color", "rgb(176, 127, 32)"); // --bf-ochre
    const blockPill = pills.filter({ hasText: "Block" }).first();
    await expect(blockPill).toHaveCSS("color", "rgb(179, 38, 30)"); // --bf-red
  });

  test("kind filter narrows to just Blocks", async ({ alicePage }) => {
    await alicePage.goto("/ui/audit");
    await alicePage.getByTestId("audit-kind-filter-block").click();
    const rows = alicePage.getByTestId("audit-log-row");
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText("Block");
  });

  test("actor filter narrows to dave's denied reveal only", async ({ alicePage }) => {
    await alicePage.goto("/ui/audit");
    await alicePage.getByTestId("audit-actor-filter").selectOption("dave");
    const rows = alicePage.getByTestId("audit-log-row");
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText("dave");
  });

  test("time-range filter excludes the seeded 2020 event by default, includes it under All time", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/audit");
    await expect(alicePage.getByTestId("audit-log-table")).not.toContainText("hit_count=0");
    await alicePage.getByTestId("audit-time-filter").selectOption("all");
    // All 5 seeded records are real-space crossings/refusals — all visible under "All time".
    await expect(alicePage.getByTestId("audit-log-row")).toHaveCount(5);
    await expect(alicePage.getByTestId("audit-log-table")).toContainText("hit_count=0");
  });
});

test.describe("Audit log — dave (curator only, no viewer)", () => {
  test("shows the locked state, not an error", async ({ davePage }) => {
    await davePage.goto("/ui/audit");
    await expect(davePage.getByTestId("audit-log-locked")).toContainText(
      "You need the viewer role"
    );
    await expect(davePage.getByTestId("audit-log-table")).toHaveCount(0);
  });
});
