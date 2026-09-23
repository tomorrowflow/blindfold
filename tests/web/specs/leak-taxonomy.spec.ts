import { test as base, expect } from "@playwright/test";

// ADR-0010's #417 amendment, browser-side (issue #417): `leak_detected`'s one
// sub_reason used to cover two causes with opposite remedies. Both causes are
// real-blinder-miss safety-net paths -- unreachable by posting ordinary text at
// a healthy fixture (detection is supposed to catch both first), so this port's
// own block_history is seeded directly through the real `_leak_gate_or_block`
// funnel (serve_fixture.py's LEAK_TAXONOMY block) rather than via a live POST.
// That seeding call is the exact function `/v1/messages`'s own block path
// invokes, so the BlockRecord shapes reaching this page are the real production
// shapes, not a hand-typed stand-in.
//
// Covers the Home/Status half of #417's diff: RecentBlocksTable's "What to do"
// column is a pure client-side lookup (BLOCK_REMEDY_BY_SUB_REASON, keyed by
// sub_reason) -- this proves the two new/changed entries actually reach the
// rendered DOM attached to the right row, and that neither remedy (nor any
// other part of the rendered page) ever carries the blocked request's real
// entity value.
//
// review-inbox.spec.ts's own "reject states its consequence" test covers this
// issue's other half (the Reject button's disclosed consequence).

const LEAK_TAXONOMY_BASE_URL = "http://127.0.0.1:8961";
const DEFECT_REAL = "Rutherford Kessling";
const REVIEW_INBOX_REAL = "Klaus Bergmann";

const test = base.extend<{ alicePage: import("@playwright/test").Page }>({
  alicePage: async ({ browser }, use) => {
    const context = await browser.newContext({
      baseURL: LEAK_TAXONOMY_BASE_URL,
      extraHTTPHeaders: { "x-blindfold-identity": "alice" },
    });
    const page = await context.newPage();
    await use(page);
    await context.close();
  },
});

test.describe("Recent blocks — leak_detected taxonomy split", () => {
  test("both causes appear as separate rows with their own differentiated remedy", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/status");
    const table = alicePage.getByTestId("blocks-table");
    await expect(table).toBeVisible();
    await expect(alicePage.getByTestId("blocks-row")).toHaveCount(2);

    // Defect cause (sub_reason "leak_detected"): the pre-existing code, now
    // scoped to mean only the blinder-miss cause -- "report it", never a
    // curation choice, same framing as detection_internal.
    const defectRow = alicePage.getByTestId("blocks-row").filter({
      hasText: "This is a Blindfold defect, not a curation choice",
    });
    await expect(defectRow).toHaveCount(1);
    await expect(defectRow).toContainText(
      "a known real value was not blindfolded before egress"
    );
    await expect(defectRow).toContainText("Please report it");

    // Curation cause (new sub_reason "leak_detected_review_inbox"): names both
    // verdicts and reject's process-global, permanent consequence -- a
    // human-chosen fail-open is only real if the human is told it is one.
    const curationRow = alicePage.getByTestId("blocks-row").filter({
      hasText: "A pending review-inbox row matches this value",
    });
    await expect(curationRow).toHaveCount(1);
    await expect(curationRow).toContainText("Confirm it to keep the value protected");
    await expect(curationRow).toContainText("reject it to add it to the allowlist");
    await expect(curationRow).toContainText("never blindfolded again");
    await expect(curationRow).toContainText("every workspace");
    await expect(curationRow).toContainText("Curate the row in the review inbox");
  });

  test("neither blocked request's real entity value ever reaches the rendered page", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/status");
    await expect(alicePage.getByTestId("blocks-table")).toBeVisible();

    const bodyText = await alicePage.locator("body").innerText();
    expect(bodyText).not.toContain(DEFECT_REAL);
    expect(bodyText).not.toContain(REVIEW_INBOX_REAL);

    // SPA privacy: authorized-only re-identification is N/A on this view --
    // scrubbed_reason/remedy are scrubbed-by-construction and ungated (ADR-0027,
    // /v1/status carries no role check at all), so there is no authorized-vs-
    // unauthorized split to exercise here; the property that matters is "never
    // leaks to anyone", already pinned above.
  });

  test("retryability differentiates the two causes: leak_detected is not-retryable, the curation cause stays unknown (issue #425)", async ({
    alicePage,
  }) => {
    // ADR-0057's #425 amendment: only causes deterministic by construction claim
    // "not-retryable" -- leak_detected (the defect cause) is one; the curation
    // cause (a human-pending review-inbox match) is not, and defaults to
    // "unknown" like every other non-deterministic sub_reason. block_history's
    // own retryability field is asserted here, not the rendered table -- the
    // page never surfaces it as a cell (RecentBlocksTable renders scrubbed_reason
    // and the remedy lookup only), so the browser-observable surface for this
    // property is the /v1/status response the page itself fetched, not the DOM.
    const [statusResponse] = await Promise.all([
      alicePage.waitForResponse(
        (res) => res.url().includes("/v1/status") && res.request().method() === "GET"
      ),
      alicePage.goto("/ui/status"),
    ]);
    const statusBody = await statusResponse.json();
    const records: Array<{ sub_reason: string; retryability: string }> = statusBody.blocks.recent;

    const defect = records.find((r) => r.sub_reason === "leak_detected");
    const curation = records.find((r) => r.sub_reason === "leak_detected_review_inbox");
    expect(defect?.retryability).toBe("not-retryable");
    expect(curation?.retryability).toBe("unknown");

    // Not entity content, but confirm it never smuggled any anyway.
    const serialized = JSON.stringify(statusBody);
    expect(serialized).not.toContain(DEFECT_REAL);
    expect(serialized).not.toContain(REVIEW_INBOX_REAL);
  });

  test("loading Home/Status makes no cross-origin request, and none carries the blocked values", async ({
    alicePage,
  }) => {
    const requests: string[] = [];
    alicePage.on("request", (req) => requests.push(req.url()));

    await alicePage.goto("/ui/status");
    await expect(alicePage.getByTestId("blocks-table")).toBeVisible();

    const origin = new URL(LEAK_TAXONOMY_BASE_URL).origin;
    for (const url of requests) {
      expect(new URL(url).origin).toBe(origin);
      expect(url).not.toContain(DEFECT_REAL);
      expect(url).not.toContain(REVIEW_INBOX_REAL);
    }

    // Audit-on-decrypt is N/A on this view: Home/Status never re-identifies
    // (decrypts) anything -- it only renders already-scrubbed block records --
    // so there is no decrypt action here to produce an audit record for.
  });
});
