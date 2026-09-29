import { test as base, expect, type Page } from "@playwright/test";

// Reject scope choice + Rejected view (ADR-0010's #423 amendment, issue #444):
// the reject dialog always shows both "This workspace" (preselected) and "All
// workspaces", the #417 disclosure follows the selection, and the request always
// carries the chosen scope. The Rejected view (a tab inside this same
// destination, not under Settings) lists the workspace's own learned entries
// plus every all-workspaces entry, with Remove (no confirmation, mirrors
// EdgeChips' own immediate-delete precedent) and, for workspace-scoped rows,
// Widen to all workspaces (a chosen fail-open, so it needs confirmation with
// the same disclosure).
//
// Runs against its own dedicated fixture (port 8963, serve_fixture.py's
// REJECT_SCOPE state) rather than the shared port: review-inbox.spec.ts's own
// confirm/reject sequence already consumes both of the shared port's seeded
// candidates, and this file's "Rejected view" describe block needs pre-seeded
// learned entries the shared port's allowlist never carries.
//
// Sequential (fullyParallel: false, workers: 1, playwright.config.ts): the two
// describe blocks below run top-to-bottom, and the Rejected view's own listing
// test intentionally counts the two entries the dialog-flow describe block
// above it just rejected, alongside the three pre-seeded ones.

const BASE_URL = "http://127.0.0.1:8963";

const REJECT_SCOPE_WORKSPACE_REAL = "Priya Kestrel";
const REJECT_SCOPE_ALL_REAL = "Talia Renwick";
const REJECT_SCOPE_REMOVE_TOKEN = "Wendell Okafor";
const REJECT_SCOPE_WIDEN_TOKEN = "Briony Castellan";
const REJECT_SCOPE_ALLWS_TOKEN = "Solenne Marchetti";

const test = base.extend<{ alicePage: Page; davePage: Page }>({
  alicePage: async ({ browser }, use) => {
    const context = await browser.newContext({
      baseURL: BASE_URL,
      extraHTTPHeaders: { "x-blindfold-identity": "alice" },
    });
    const page = await context.newPage();
    await use(page);
    await context.close();
  },
  // dave holds ONLY curator on WORKSPACE ("acme") -- no viewer, so he can select
  // the workspace (curator is enough to appear in the switcher) but is refused
  // both the review-inbox list AND (browser-verify, issue #444) the Rejected
  // view's own learned-allowlist list, which renders the exact same sensitivity
  // class of real value (serve_fixture.py's build_app grants this on every port,
  // REJECT_SCOPE only swaps the review_inbox/allowlist instances, never rbac).
  davePage: async ({ browser }, use) => {
    const context = await browser.newContext({
      baseURL: BASE_URL,
      extraHTTPHeaders: { "x-blindfold-identity": "dave" },
    });
    const page = await context.newPage();
    await use(page);
    await context.close();
  },
});

test.describe("reject dialog scope choice", () => {
  test("always shows both scopes with 'This workspace' preselected, and the disclosure follows the selection", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/inbox");
    const item = alicePage
      .getByTestId("review-inbox-item")
      .filter({ hasText: REJECT_SCOPE_WORKSPACE_REAL });
    await item.getByRole("button", { name: "Reject" }).click();

    const dialog = alicePage.getByTestId("reject-dialog");
    await expect(dialog).toBeVisible();
    const workspaceRadio = dialog.getByTestId("reject-scope-workspace");
    const allRadio = dialog.getByTestId("reject-scope-all");
    await expect(workspaceRadio).toBeVisible();
    await expect(allRadio).toBeVisible();
    await expect(workspaceRadio).toBeChecked();
    await expect(allRadio).not.toBeChecked();

    const disclosure = dialog.getByTestId("reject-dialog-disclosure");
    await expect(disclosure).toContainText("Never blindfolded again");
    await expect(disclosure).not.toContainText("every workspace");

    await allRadio.check();
    await expect(allRadio).toBeChecked();
    await expect(disclosure).toContainText("every subsequent request");
    await expect(disclosure).toContainText("every workspace");

    await workspaceRadio.check();
    await expect(disclosure).not.toContainText("every workspace");

    // Cancel leaves the item in the inbox for the next test to reject for real.
    await dialog.getByTestId("reject-dialog-cancel").click();
    await expect(dialog).toBeHidden();
    await expect(item).toBeVisible();
  });

  test("rejecting with the default scope sends {scope: \"workspace\"} and removes the item", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/inbox");
    const item = alicePage
      .getByTestId("review-inbox-item")
      .filter({ hasText: REJECT_SCOPE_WORKSPACE_REAL });
    await item.getByRole("button", { name: "Reject" }).click();

    const dialog = alicePage.getByTestId("reject-dialog");
    const [rejectRequest] = await Promise.all([
      alicePage.waitForRequest(
        (req) => req.url().includes("/reject") && req.method() === "POST"
      ),
      dialog.getByTestId("reject-dialog-confirm").click(),
    ]);

    expect(rejectRequest.postDataJSON()).toEqual({ scope: "workspace" });
    await expect(dialog).toBeHidden();
    await expect(
      alicePage.getByTestId("review-inbox-item").filter({ hasText: REJECT_SCOPE_WORKSPACE_REAL })
    ).toHaveCount(0);
  });

  test("choosing 'All workspaces' sends {scope: \"all\"} and removes the item", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/inbox");
    const item = alicePage
      .getByTestId("review-inbox-item")
      .filter({ hasText: REJECT_SCOPE_ALL_REAL });
    await item.getByRole("button", { name: "Reject" }).click();

    const dialog = alicePage.getByTestId("reject-dialog");
    await dialog.getByTestId("reject-scope-all").check();

    const [rejectRequest] = await Promise.all([
      alicePage.waitForRequest(
        (req) => req.url().includes("/reject") && req.method() === "POST"
      ),
      dialog.getByTestId("reject-dialog-confirm").click(),
    ]);

    expect(rejectRequest.postDataJSON()).toEqual({ scope: "all" });
    await expect(alicePage.getByTestId("review-inbox-item")).toHaveCount(0);
  });
});

test.describe("Rejected view", () => {
  test("lists workspace and all-workspaces learned entries with their scope", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/inbox");
    await alicePage.getByTestId("review-inbox-tab-rejected").click();

    // The 3 pre-seeded entries plus the 2 just rejected above (dialog-flow
    // describe block, same server, run order preserved -- workers: 1).
    await expect(alicePage.getByTestId("rejected-item")).toHaveCount(5);

    const removeRow = alicePage
      .getByTestId("rejected-item")
      .filter({ hasText: REJECT_SCOPE_REMOVE_TOKEN });
    await expect(removeRow.getByTestId("rejected-item-scope")).toHaveText("This workspace");
    await expect(removeRow.getByTestId("rejected-item-widen")).toBeVisible();

    const allWsRow = alicePage
      .getByTestId("rejected-item")
      .filter({ hasText: REJECT_SCOPE_ALLWS_TOKEN });
    await expect(allWsRow.getByTestId("rejected-item-scope")).toHaveText("All workspaces");
    await expect(allWsRow.getByTestId("rejected-item-widen")).toHaveCount(0);

    const workspaceRejectRow = alicePage
      .getByTestId("rejected-item")
      .filter({ hasText: REJECT_SCOPE_WORKSPACE_REAL });
    await expect(workspaceRejectRow.getByTestId("rejected-item-scope")).toHaveText(
      "This workspace"
    );

    const allRejectRow = alicePage
      .getByTestId("rejected-item")
      .filter({ hasText: REJECT_SCOPE_ALL_REAL });
    await expect(allRejectRow.getByTestId("rejected-item-scope")).toHaveText("All workspaces");
  });

  test("Remove calls the API and drops the row, without a confirmation prompt", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/inbox");
    await alicePage.getByTestId("review-inbox-tab-rejected").click();

    const row = alicePage
      .getByTestId("rejected-item")
      .filter({ hasText: REJECT_SCOPE_REMOVE_TOKEN });
    const [removeRequest] = await Promise.all([
      alicePage.waitForRequest(
        (req) =>
          req.url().includes(encodeURIComponent(REJECT_SCOPE_REMOVE_TOKEN)) &&
          req.method() === "DELETE"
      ),
      row.getByTestId("rejected-item-remove").click(),
    ]);

    expect(removeRequest.method()).toBe("DELETE");
    await expect(
      alicePage.getByTestId("rejected-item").filter({ hasText: REJECT_SCOPE_REMOVE_TOKEN })
    ).toHaveCount(0);
  });

  test("Widen needs confirmation with the all-workspaces disclosure, then widens the entry", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/inbox");
    await alicePage.getByTestId("review-inbox-tab-rejected").click();

    const row = alicePage
      .getByTestId("rejected-item")
      .filter({ hasText: REJECT_SCOPE_WIDEN_TOKEN });
    await row.getByTestId("rejected-item-widen").click();

    const dialog = alicePage.getByTestId("widen-dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByTestId("widen-dialog-disclosure")).toContainText("every workspace");
    await expect(dialog.getByTestId("widen-dialog-disclosure")).toContainText(
      "every subsequent request"
    );

    // Cancelling makes no request and leaves the row workspace-scoped.
    await dialog.getByTestId("widen-dialog-cancel").click();
    await expect(dialog).toBeHidden();
    await expect(row.getByTestId("rejected-item-scope")).toHaveText("This workspace");

    await row.getByTestId("rejected-item-widen").click();
    const [widenRequest] = await Promise.all([
      alicePage.waitForRequest(
        (req) =>
          req.url().includes(encodeURIComponent(REJECT_SCOPE_WIDEN_TOKEN)) &&
          req.url().includes("/widen") &&
          req.method() === "POST"
      ),
      alicePage.getByTestId("widen-dialog-confirm").click(),
    ]);
    expect(widenRequest.method()).toBe("POST");

    const widenedRow = alicePage
      .getByTestId("rejected-item")
      .filter({ hasText: REJECT_SCOPE_WIDEN_TOKEN });
    await expect(widenedRow.getByTestId("rejected-item-scope")).toHaveText("All workspaces");
    await expect(widenedRow.getByTestId("rejected-item-widen")).toHaveCount(0);
  });

  test("makes no third-party egress -- every request stays same-origin", async ({ alicePage }) => {
    const requestHosts = new Set<string>();
    alicePage.on("request", (req) => requestHosts.add(new URL(req.url()).host));

    await alicePage.goto("/ui/inbox");
    await alicePage.getByTestId("review-inbox-tab-rejected").click();
    await expect(alicePage.getByTestId("rejected-list")).toBeVisible();

    const firstPartyHost = new URL(BASE_URL).host;
    const thirdParty = [...requestHosts].filter((host) => host !== firstPartyHost);
    expect(thirdParty, `unexpected non-loopback requests: ${thirdParty.join(", ")}`).toEqual([]);
  });
});

test.describe("Rejected view — dave (curator only, no viewer)", () => {
  // Browser-verify privacy property (issue #444): a learned-allowlist token IS a
  // real entity value (app.py's reject_review_item stores item.real verbatim;
  // list_learned_allowlist_entries's own docstring: "a mistaken reject may be a
  // real value, the same sensitivity class" the review-inbox list already
  // viewer-gates). The Rejected view's tab renders regardless of role (only the
  // Inbox tab's content branches on `locked`), so this pins that the API's own
  // viewer gate -- not any client-side check -- is what keeps the three seeded
  // real tokens (REJECT_SCOPE_REMOVE_TOKEN / WIDEN_TOKEN / ALLWS_TOKEN) out of
  // both the DOM and the network response dave's browser actually receives.
  test("never shows or receives a real learned-allowlist value for an unauthorized viewer", async ({
    davePage,
  }) => {
    await davePage.goto("/ui/inbox");

    const [learnedResponse] = await Promise.all([
      davePage.waitForResponse((res) => res.url().includes("/v1/management/allowlist/learned")),
      davePage.getByTestId("review-inbox-tab-rejected").click(),
    ]);

    expect(learnedResponse.ok()).toBeFalsy();
    expect(learnedResponse.status()).toBe(403);
    const responseText = await learnedResponse.text();
    for (const token of [
      REJECT_SCOPE_REMOVE_TOKEN,
      REJECT_SCOPE_WIDEN_TOKEN,
      REJECT_SCOPE_ALLWS_TOKEN,
      REJECT_SCOPE_WORKSPACE_REAL,
      REJECT_SCOPE_ALL_REAL,
    ]) {
      expect(responseText).not.toContain(token);
      await expect(davePage.locator("body")).not.toContainText(token);
    }

    // The client swallows the 403 into an empty list (RejectedList's own
    // `.catch(() => setEntries([]))`) rather than a distinct locked state --
    // a UX ambiguity, not a privacy leak: no row, and therefore no Remove/Widen
    // affordance carrying a real value, is ever rendered.
    await expect(davePage.getByTestId("rejected-item")).toHaveCount(0);
    await expect(davePage.getByTestId("rejected-list-empty")).toBeVisible();
  });
});
