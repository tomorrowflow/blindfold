import { test, expect } from "./fixtures";

// Review inbox shell migration (issue #99): the review inbox moves from the legacy
// embedded Vue page (`/ui/review-inbox`, retired) into the unified shell at `/ui/inbox`,
// restyled to the token set. The sidebar's Review inbox nav item carries a lime
// pending-count badge fed by the same `review_inbox.pending` count `/v1/status`
// exposes (issue #92) — confirm/reject behavior itself is unchanged (ported from the
// legacy page's tests, `tests/test_review_inbox_spa.py` / `test_review_inbox_learning_loop.py`).
//
// Fixture seeds two provisional candidates (serve_fixture.py): "Klaus Bergmann" and
// "Nordwind Systems".

test.describe("review inbox", () => {
  test("sidebar shows the pending-count badge matching /v1/status", async ({ page }) => {
    await page.goto("/ui/");
    await expect(page.getByTestId("review-inbox-badge")).toHaveText("2");
  });

  test("inbox lists provisional candidates with real value, mono surrogate, and context", async ({
    page,
  }) => {
    await page.goto("/ui/inbox");
    await expect(page.getByRole("heading", { name: "Review inbox" })).toBeVisible();

    const items = page.getByTestId("review-inbox-item");
    await expect(items).toHaveCount(2);

    const klaus = page.getByTestId("review-inbox-item").filter({ hasText: "Klaus Bergmann" });
    await expect(klaus).toBeVisible();
    await expect(klaus).toContainText("Please brief Klaus Bergmann on the merger tomorrow.");
    await expect(klaus.getByRole("button", { name: "Confirm" })).toBeVisible();
    await expect(klaus.getByRole("button", { name: "Reject" })).toBeVisible();
  });

  test("confirming an item removes it from the list and decrements the sidebar badge", async ({
    page,
  }) => {
    await page.goto("/ui/inbox");
    const klaus = page.getByTestId("review-inbox-item").filter({ hasText: "Klaus Bergmann" });
    await klaus.getByRole("button", { name: "Confirm" }).click();

    await expect(page.getByTestId("review-inbox-item")).toHaveCount(1);
    await expect(page.getByTestId("review-inbox-badge")).toHaveText("1");
  });

  test("rejecting the last item shows the empty state and clears the sidebar badge", async ({
    page,
  }) => {
    // Runs after the "confirming an item" test above (shared server, sequential
    // workers) — "Klaus Bergmann" is already triaged, so "Nordwind Systems" is the
    // one remaining item.
    await page.goto("/ui/inbox");
    await expect(page.getByTestId("review-inbox-item")).toHaveCount(1);

    await page
      .getByTestId("review-inbox-item")
      .filter({ hasText: "Nordwind Systems" })
      .getByRole("button", { name: "Reject" })
      .click();

    await expect(page.getByTestId("review-inbox-empty")).toBeVisible();
    await expect(page.getByTestId("review-inbox-empty")).toContainText("Inbox clear");
    await expect(page.getByTestId("review-inbox-badge")).toHaveCount(0);
  });
});
