import { test, expect, auditEventsFor } from "./fixtures";

// Processing trace view (ADR-0035, issue #151): a live follow-along view of what
// the proxy did per request, GET /v1/management/processing-trace, viewer-gated +
// workspace-scoped the same way the audit log is (#16). serve_fixture.py seeds one
// Passed, one Blocked, and one Upstream-error record for the "acme" workspace.
//
// Issue #154 (audited Reveal + Review-inbox deep-link): the seeded "passed"
// record's hop-0 carries two surrogates exercising two of the three reveal
// lifecycles -- "Berta Vogel" (confirmed: seeded into the trace's own
// SurrogateMapping + reidentify store) and "Igor Talvik" (rejected: recognized
// by neither store). Hop-1 carries the review inbox's own first provisional
// surrogate (pending: still awaiting triage).
const TRACE_HOP_SURROGATE = "Berta Vogel";
const TRACE_HOP_REAL = "Klaus Weber";
const TRACE_HOP_REJECTED_SURROGATE = "Igor Talvik";
const TRACE_HOP_PENDING_SURROGATE = "Alex Brenner"; // review inbox's first provisional pool entry

test.describe("Processing trace — alice (holds viewer)", () => {
  test("renders header, subtitle and the seven-column grid", async ({ alicePage }) => {
    await alicePage.goto("/ui/processing-trace");
    const view = alicePage.getByTestId("processing-trace-page");
    await expect(view.locator("h1")).toHaveText("Processing trace");
    await expect(view).toContainText("never a");
    const headers = alicePage.locator("[data-testid='processing-trace-table'] th");
    await expect(headers).toHaveText([
      "Outcome",
      "Time",
      "Total",
      "Blindfold / Upstream",
      "Detected",
      "L3",
      "Hops",
    ]);
  });

  test("Total and Blindfold / Upstream columns split the seeded passed row's timing", async ({
    alicePage,
  }) => {
    // Issue #158: the seeded "passed" record is duration_ms=118, upstream_duration_ms=15
    // -- the split must show the derived blindfold-side share (103ms), never a
    // re-estimated or stored value.
    await alicePage.goto("/ui/processing-trace");
    const totalCells = alicePage.getByTestId("processing-trace-row-total");
    await expect(totalCells).toHaveCount(3);
    await expect(totalCells.filter({ hasText: "118ms" })).toHaveCount(1);

    const splitCells = alicePage.getByTestId("processing-trace-row-split");
    await expect(splitCells.filter({ hasText: "blindfold 103ms / upstream 15ms" })).toHaveCount(1);
    // The seeded "blocked" record never reached upstream (upstream_duration_ms=None)
    // -- the whole total is attributed to blindfold, no "/ upstream" suffix. The
    // other two seeded rows (passed, upstream_error) both did reach upstream.
    await expect(splitCells.filter({ hasText: "blindfold 9ms" })).toHaveCount(1);
    await expect(splitCells.filter({ hasText: "/ upstream" })).toHaveCount(2);
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

  test("L3 column shows provider + timing when L3 ran, em-dash otherwise", async ({
    alicePage,
  }) => {
    // Issue #153: the seeded "passed" record ran L3 through "ollama" (42ms); the
    // seeded "blocked"/"upstream_error" records never blindfolded a hop at all, so
    // their L3 cell must read the em-dash, never a stale/zero value.
    await alicePage.goto("/ui/processing-trace");
    const l3Cells = alicePage.getByTestId("processing-trace-row-l3");
    await expect(l3Cells).toHaveCount(3);
    await expect(l3Cells.filter({ hasText: "ollama" })).toHaveText("ollama (42ms)");
    const dashes = await l3Cells.allTextContents();
    expect(dashes.filter((text) => text === "—")).toHaveLength(2);
  });

  test("clicking a row expands inline into one card per hop, in pipeline order", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/processing-trace");
    await expect(alicePage.getByTestId("processing-trace-hop-card")).toHaveCount(0);

    // The seeded "passed" row carries 2 hops (system, user).
    const hopsToggle = alicePage
      .getByTestId("processing-trace-row-hops-toggle")
      .filter({ hasText: "2" });
    await hopsToggle.click();

    const cards = alicePage.getByTestId("processing-trace-hop-card");
    await expect(cards).toHaveCount(2);
    await expect(cards.nth(0)).toContainText("system");
    await expect(cards.nth(1)).toContainText("user");
    // The user hop's L3 breakdown (1 confirmed, 1 dismissed, 2 suppressed) and its
    // injected-surrogate chips render -- scrubbed tokens only, never a real value.
    await expect(cards.nth(1)).toContainText("1 confirmed, 1 dismissed, 2 suppressed");
    await expect(cards.nth(1)).toContainText(TRACE_HOP_PENDING_SURROGATE);

    // Collapsing hides the cards again without losing the seeded rows.
    await hopsToggle.click();
    await expect(alicePage.getByTestId("processing-trace-hop-card")).toHaveCount(0);
    await expect(alicePage.getByTestId("processing-trace-row")).toHaveCount(3);
  });

  test("a confirmed surrogate exposes the audited Reveal control, revealing logs an audit event", async ({
    alicePage,
    baseURL,
  }) => {
    // Issue #154: reuses the existing Re-identify path (not a new endpoint) --
    // same reveal-btn/reveal-confirm/reveal-value affordance as the entity list.
    await alicePage.goto("/ui/processing-trace");
    await alicePage
      .getByTestId("processing-trace-row-hops-toggle")
      .filter({ hasText: "2" })
      .click();
    const systemCard = alicePage.getByTestId("processing-trace-hop-card").first();
    const chip = systemCard.locator(".bf-trace-hop-surrogate-chip", {
      hasText: TRACE_HOP_SURROGATE,
    });
    await chip.getByTestId("reveal-btn").click();
    await chip.getByTestId("reveal-confirm").click();
    await expect(chip.getByTestId("reveal-value")).toHaveText(`real: ${TRACE_HOP_REAL}`);

    const reveals = await auditEventsFor(baseURL!, "re-identified", "alice");
    expect(reveals.some((r) => r.reason.includes(TRACE_HOP_SURROGATE))).toBe(true);
  });

  test("a pending novel candidate renders a Review-inbox deep-link instead of Reveal", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/processing-trace");
    await alicePage
      .getByTestId("processing-trace-row-hops-toggle")
      .filter({ hasText: "2" })
      .click();
    const userCard = alicePage.getByTestId("processing-trace-hop-card").nth(1);
    const chip = userCard.locator(".bf-trace-hop-surrogate-chip", {
      hasText: TRACE_HOP_PENDING_SURROGATE,
    });
    await expect(chip.getByTestId("reveal-btn")).toHaveCount(0);
    const link = chip.getByTestId("processing-trace-pending-review-link");
    await expect(link).toHaveText("Pending review →");
    await link.click();
    await expect(alicePage).toHaveURL(/\/ui\/inbox$/);
    await expect(alicePage.getByTestId("review-inbox-page")).toBeVisible();
  });

  test("a surrogate recognized by neither store renders no reveal affordance", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/processing-trace");
    await alicePage
      .getByTestId("processing-trace-row-hops-toggle")
      .filter({ hasText: "2" })
      .click();
    const systemCard = alicePage.getByTestId("processing-trace-hop-card").first();
    const chip = alicePage
      .getByTestId("processing-trace-rejected-surrogate-chip")
      .filter({ hasText: TRACE_HOP_REJECTED_SURROGATE });
    await expect(chip).toBeVisible();
    await expect(systemCard.getByTestId("reveal-btn")).toHaveCount(1); // only the confirmed chip's
    await expect(chip.getByTestId("processing-trace-pending-review-link")).toHaveCount(0);
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
