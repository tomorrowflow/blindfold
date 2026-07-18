import { test, expect } from "./fixtures";

// Review inbox shell migration (issue #99): the review inbox moves from the legacy
// embedded Vue page (`/ui/review-inbox`, retired) into the unified shell at `/ui/inbox`,
// restyled to the token set. The sidebar's Review inbox nav item carries a lime
// pending-count badge fed by the same `review_inbox.pending` count `/v1/status`
// exposes (issue #92, deliberately NOT workspace-gated) — confirm/reject behavior
// itself is unchanged (ported from the legacy page's tests,
// `tests/test_review_inbox_spa.py` / `test_review_inbox_learning_loop.py`).
//
// GET /v1/management/review-inbox is now `viewer`-gated (ADR-0035, issue #152) —
// same gate as the audit log view (audit-log-shell.spec.ts) — so the list/triage
// specs below run as `alicePage` (holds `viewer` on WORKSPACE per serve_fixture.py);
// a separate describe block below covers the locked treatment for a caller without it.
//
// Fixture seeds two provisional candidates (serve_fixture.py): "Klaus Bergmann" and
// "Nordwind Systems".

test.describe("review inbox — alice (holds viewer)", () => {
  test("sidebar shows the pending-count badge matching /v1/status", async ({ alicePage }) => {
    await alicePage.goto("/ui/");
    await expect(alicePage.getByTestId("review-inbox-badge")).toHaveText("2");
  });

  test("header carries the comp subtitle and constrains content to an 820px centered column", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/inbox");
    await expect(alicePage.getByRole("heading", { name: "Review inbox" })).toBeVisible();
    await expect(alicePage.getByText(
      "Provisional surrogates detected in traffic. Confirm to keep, or reject to discard the candidate."
    )).toBeVisible();

    const column = alicePage.getByTestId("review-inbox-page");
    await expect(column).toHaveCSS("max-width", "820px");
    const columnBox = await column.boundingBox();
    const mainBox = await alicePage.locator(".bf-main").boundingBox();
    if (!columnBox || !mainBox) throw new Error("missing bounding box");
    const leftGap = columnBox.x - mainBox.x;
    const rightGap = mainBox.x + mainBox.width - (columnBox.x + columnBox.width);
    expect(Math.abs(leftGap - rightGap)).toBeLessThanOrEqual(2);
  });

  test("inbox lists provisional candidates with real value, mono surrogate, and context", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/inbox");
    await expect(alicePage.getByRole("heading", { name: "Review inbox" })).toBeVisible();

    const items = alicePage.getByTestId("review-inbox-item");
    await expect(items).toHaveCount(2);

    const klaus = alicePage.getByTestId("review-inbox-item").filter({ hasText: "Klaus Bergmann" });
    await expect(klaus).toBeVisible();
    await expect(klaus).toContainText("Please brief Klaus Bergmann on the merger tomorrow.");

    const confirmBtn = klaus.getByRole("button", { name: "Confirm" });
    await expect(confirmBtn).toBeVisible();
    await expect(confirmBtn.locator("svg")).toBeVisible();
    await expect(klaus.getByRole("button", { name: "Reject" })).toBeVisible();

    await expect(klaus).toHaveCSS("border-radius", "13px");
    await expect(klaus.locator(".bf-review-inbox-item-surrogate")).toHaveCSS(
      "font-family",
      /Mono/
    );
  });

  test("candidate span is highlighted in place within the context, with a neutral tint", async ({
    alicePage,
  }) => {
    // ADR-0035 decision 11 (issue #155): context_offset (backend-derived) lets
    // the SPA wrap exactly the candidate span, not the whole context sentence.
    await alicePage.goto("/ui/inbox");

    const klaus = alicePage.getByTestId("review-inbox-item").filter({ hasText: "Klaus Bergmann" });
    const highlight = klaus.getByTestId("review-inbox-item-highlight");
    await expect(highlight).toHaveText("Klaus Bergmann");
    await expect(klaus.getByTestId("review-inbox-item-context")).toContainText(
      "Please brief Klaus Bergmann on the merger tomorrow."
    );

    // Neutral tint: the border-soft/border tokens, never ochre/red/curator-green.
    const backgroundColor = await highlight.evaluate(
      (el) => getComputedStyle(el).backgroundColor
    );
    const ochre = "rgb(250, 246, 236)"; // --bf-ochre-bg
    const red = "rgb(179, 38, 30)"; // --bf-red
    const curatorGreen = "rgb(231, 243, 236)"; // --bf-curator-bg
    expect(backgroundColor).not.toBe(ochre);
    expect(backgroundColor).not.toBe(red);
    expect(backgroundColor).not.toBe(curatorGreen);
  });

  test("confirming an item removes it from the list and decrements the sidebar badge", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/inbox");
    const klaus = alicePage.getByTestId("review-inbox-item").filter({ hasText: "Klaus Bergmann" });
    await klaus.getByRole("button", { name: "Confirm" }).click();

    await expect(alicePage.getByTestId("review-inbox-item")).toHaveCount(1);
    await expect(alicePage.getByTestId("review-inbox-badge")).toHaveText("1");
  });

  test("rejecting the last item shows the empty state and clears the sidebar badge", async ({
    alicePage,
  }) => {
    // Runs after the "confirming an item" test above (shared server, sequential
    // workers) — "Klaus Bergmann" is already triaged, so "Nordwind Systems" is the
    // one remaining item.
    await alicePage.goto("/ui/inbox");
    await expect(alicePage.getByTestId("review-inbox-item")).toHaveCount(1);

    await alicePage
      .getByTestId("review-inbox-item")
      .filter({ hasText: "Nordwind Systems" })
      .getByRole("button", { name: "Reject" })
      .click();

    const empty = alicePage.getByTestId("review-inbox-empty");
    await expect(empty).toBeVisible();
    await expect(alicePage.getByTestId("review-inbox-empty-badge")).toBeVisible();
    await expect(empty.getByRole("heading", { name: "Inbox clear" })).toBeVisible();
    await expect(empty).toContainText("Every provisional candidate has been reviewed.");
    await expect(alicePage.getByTestId("review-inbox-badge")).toHaveCount(0);
  });
});

test.describe("review inbox — dave (curator only, no viewer)", () => {
  test("shows the locked state, not an error or the candidates' real values", async ({
    davePage,
  }) => {
    await davePage.goto("/ui/inbox");
    await expect(davePage.getByTestId("review-inbox-locked")).toContainText(
      "You need the viewer role"
    );
    await expect(davePage.getByTestId("review-inbox-item")).toHaveCount(0);
  });
});
