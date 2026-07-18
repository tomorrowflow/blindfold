import { test, expect } from "./fixtures";

// Processing trace view (ADR-0035, issue #151): a live follow-along view of what
// the proxy did per request, GET /v1/management/processing-trace, viewer-gated +
// workspace-scoped the same way the audit log is (#16). serve_fixture.py seeds one
// Passed, one Blocked, and one Upstream-error record for the "acme" workspace.

test.describe("Processing trace — alice (holds viewer)", () => {
  test("renders header, subtitle and the three-column grid", async ({ alicePage }) => {
    await alicePage.goto("/ui/processing-trace");
    const view = alicePage.getByTestId("processing-trace-page");
    await expect(view.locator("h1")).toHaveText("Processing trace");
    await expect(view).toContainText("never a");
    const headers = alicePage.locator("[data-testid='processing-trace-table'] th");
    await expect(headers).toHaveText(["Outcome", "Time", "Detected"]);
  });

  test("shows the seeded passed, blocked and upstream-error rows", async ({ alicePage }) => {
    await alicePage.goto("/ui/processing-trace");
    const rows = alicePage.getByTestId("processing-trace-row");
    await expect(rows).toHaveCount(3);
    const outcomes = alicePage.getByTestId("processing-trace-row-outcome");
    const kinds = await outcomes.evaluateAll((els) => els.map((el) => el.getAttribute("data-outcome")));
    expect(new Set(kinds)).toEqual(new Set(["passed", "blocked", "upstream_error"]));
  });

  test("outcome chips use green for passed, red for blocked, grey for upstream error", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/processing-trace");
    const pills = alicePage.getByTestId("processing-trace-row-outcome");
    const passedPill = pills.filter({ hasText: "Passed" }).first();
    await expect(passedPill).toHaveCSS("color", "rgb(31, 138, 91)"); // --bf-ok
    const blockedPill = pills.filter({ hasText: "Blocked" }).first();
    await expect(blockedPill).toHaveCSS("color", "rgb(179, 38, 30)"); // --bf-red
    const upstreamPill = pills.filter({ hasText: "Upstream error" }).first();
    await expect(upstreamPill).toHaveCSS("color", "rgb(107, 117, 137)"); // neutral grey
  });

  test("Live | Paused pill toggles the freshness indicator", async ({ alicePage }) => {
    await alicePage.goto("/ui/processing-trace");
    await expect(alicePage.getByTestId("processing-trace-freshness")).toContainText("polled");
    await alicePage.getByTestId("processing-trace-paused-button").click();
    await expect(alicePage.getByTestId("processing-trace-freshness")).toContainText("Paused");
  });
});

test.describe("Processing trace — dave (curator only, no viewer)", () => {
  test("shows the locked state, not an error", async ({ davePage }) => {
    await davePage.goto("/ui/processing-trace");
    await expect(davePage.getByTestId("processing-trace-locked")).toContainText(
      "You need the viewer role"
    );
    await expect(davePage.getByTestId("processing-trace-table")).toHaveCount(0);
  });
});
