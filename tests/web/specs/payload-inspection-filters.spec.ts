import { test as base, expect } from "@playwright/test";
import { REAL_ORG, REAL_PERSON } from "./fixtures";

// Payload inspection list filters (issue #434, ADR-0059 amendment #431 §8):
// window-relative time presets, a per-hour histogram, an outcome filter, and
// a search over the retained (blindfolded-only) text -- all relative to the
// active retention window, never calendar ranges (§8's own rejection of the
// prototype's month presets/date-range inputs still stands).
//
// Reuses the same "payload_inspection_retained" fixture (port 8959)
// payload-inspection.spec.ts drives: two retained exchanges, one "passed"
// (leaves containing "Clara Hoffmann"/"Pinnacle Corp"/"Igor Talvik"/the
// pending surrogate) and one "blocked" ("Clara Hoffmann"/"Pinnacle Corp"
// again, ADR-0059 amendment #431's own "never sent" mark) -- both timestamped
// at real fixture-build wall-clock time, which is enough to exercise the
// outcome filter, the search box, and AND-combination/URL round-tripping
// without needing timestamp diversity. Time-preset/histogram coverage, which
// DOES need timestamp diversity, lives in the dedicated
// "payload_inspection_filters" fixture (port 8962) below.

const RETAINED_BASE_URL = "http://127.0.0.1:8959";

const retainedTest = base.extend<{ alicePage: import("@playwright/test").Page }>({
  alicePage: async ({ browser }, use) => {
    const context = await browser.newContext({
      baseURL: RETAINED_BASE_URL,
      extraHTTPHeaders: { "x-blindfold-identity": "alice" },
    });
    const page = await context.newPage();
    await use(page);
    await context.close();
  },
});

retainedTest.describe("Payload inspection — outcome filter", () => {
  retainedTest("separates sent from never-sent exchanges, and the count line reflects it", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/payload-inspection");
    const rows = alicePage.getByTestId("payload-inspection-row");
    await expect(rows).toHaveCount(2);
    await expect(alicePage.getByTestId("payload-inspection-count")).toContainText("2 of 2 exchanges");

    await alicePage.getByTestId("payload-inspection-outcome-filter-never_sent").click();
    await expect(rows).toHaveCount(1);
    await expect(rows.first().getByTestId("payload-inspection-row-outcome")).toHaveText("Never sent");
    await expect(alicePage.getByTestId("payload-inspection-count")).toContainText("1 of 2 exchanges");

    await alicePage.getByTestId("payload-inspection-outcome-filter-sent").click();
    await expect(rows).toHaveCount(1);
    await expect(rows.first().getByTestId("payload-inspection-row-outcome")).toHaveText("Sent");

    await alicePage.getByTestId("payload-inspection-outcome-filter-all").click();
    await expect(rows).toHaveCount(2);
  });

  retainedTest(
    "filtering out the currently selected exchange falls back to the newest visible row, not a stale empty detail pane",
    async ({ alicePage }) => {
      await alicePage.goto("/ui/payload-inspection");
      // Default selection is the newest exchange, which is the BLOCKED one
      // here (ts ...21, after the passed exchange's ts ...20).
      await expect(alicePage.getByTestId("retained-payload-never-sent")).toBeVisible();

      // "Sent" excludes the currently selected (blocked) exchange -- the
      // detail pane must fall back to the remaining "passed" exchange, not
      // strand on a "nothing matches" read while a row is plainly visible.
      await alicePage.getByTestId("payload-inspection-outcome-filter-sent").click();
      await expect(alicePage.getByTestId("payload-inspection-row")).toHaveCount(1);
      await expect(alicePage.getByTestId("payload-inspection-filtered-empty")).toHaveCount(0);
      await expect(alicePage.getByTestId("retained-payload-section")).toBeVisible();
      await expect(alicePage.getByTestId("retained-payload-never-sent")).toHaveCount(0);
    }
  );
});

retainedTest.describe("Payload inspection — search over blindfolded text", () => {
  retainedTest("matches a substring of the retained (blindfolded) text, across any leaf", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/payload-inspection");
    const rows = alicePage.getByTestId("payload-inspection-row");
    const search = alicePage.getByTestId("payload-inspection-search");

    // "Igor Talvik" sits only in the passed exchange's third leaf (rejected
    // surrogate) -- unlike "Clara Hoffmann"/"Pinnacle Corp", which both the
    // passed AND blocked exchanges' own leaves happen to contain.
    await search.fill("Igor Talvik");
    await expect(rows).toHaveCount(1);
    await expect(rows.first().getByTestId("payload-inspection-row-outcome")).toHaveText("Sent");
    await expect(alicePage.getByTestId("payload-inspection-count")).toContainText("1 of 2 exchanges");

    await search.fill("");
    await expect(rows).toHaveCount(2);
  });

  retainedTest("a real value that was rewritten in the exchange does not match the search", async ({
    alicePage,
  }) => {
    // REAL_PERSON ("Martin Bach") is the real referent behind PERSON_SURROGATE
    // ("Clara Hoffmann"), which IS retained and DOES match. Only the
    // blindfolded surrogate is ever stored (ADR-0059 §2) -- the real value
    // itself was never retained, so searching for it must find nothing, not
    // merely "no match today."
    await alicePage.goto("/ui/payload-inspection");
    const rows = alicePage.getByTestId("payload-inspection-row");
    const search = alicePage.getByTestId("payload-inspection-search");

    await search.fill("Clara Hoffmann");
    await expect(rows).toHaveCount(2);

    await search.fill(REAL_PERSON);
    await expect(rows).toHaveCount(0);
    await expect(alicePage.getByTestId("payload-inspection-list-filtered-empty")).toBeVisible();

    await search.fill(REAL_ORG);
    await expect(rows).toHaveCount(0);
  });
});

// Dedicated fixture (port 8962, serve_fixture.py's
// _build_payload_inspection_filters_fixture): three retained exchanges spread
// across real wall-clock time -- "recent" (20 min ago, sent), "hour_ago" (85
// min ago, blocked), "yesterday" (26h ago, sent) -- so the time presets and
// histogram have something to differentiate, unlike the RETAINED fixture
// above whose two rows both land at build time.
const FILTERS_BASE_URL = "http://127.0.0.1:8962";

const filtersTest = base.extend<{ alicePage: import("@playwright/test").Page }>({
  alicePage: async ({ browser }, use) => {
    const context = await browser.newContext({
      baseURL: FILTERS_BASE_URL,
      extraHTTPHeaders: { "x-blindfold-identity": "alice" },
    });
    const page = await context.newPage();
    await use(page);
    await context.close();
  },
});

filtersTest.describe("Payload inspection — window-relative time presets", () => {
  filtersTest(
    "each preset shows its own hit count; a preset with no hits is dimmed but still clickable",
    async ({ alicePage }) => {
      await alicePage.goto("/ui/payload-inspection");
      await expect(alicePage.getByTestId("payload-inspection-row")).toHaveCount(3);

      // "recent" is 20 minutes old -- inside the hour and today, outside 15 min.
      const fifteen = alicePage.getByTestId("payload-inspection-time-preset-15m");
      await expect(alicePage.getByTestId("payload-inspection-time-preset-15m-count")).toHaveText("0");
      await expect(fifteen).toHaveClass(/bf-payload-inspection-preset-chip--dimmed/);

      await expect(alicePage.getByTestId("payload-inspection-time-preset-1h-count")).toHaveText("1");
      await expect(alicePage.getByTestId("payload-inspection-time-preset-today-count")).toHaveText("2");
      await expect(alicePage.getByTestId("payload-inspection-time-preset-all-count")).toHaveText("3");

      // Dimmed does not mean disabled: clicking the zero-hit chip still
      // narrows the list (to nothing), it doesn't no-op.
      await fifteen.click();
      await expect(alicePage.getByTestId("payload-inspection-row")).toHaveCount(0);
      await expect(alicePage.getByTestId("payload-inspection-list-filtered-empty")).toBeVisible();
      await expect(fifteen).toHaveAttribute("aria-pressed", "true");
    }
  );

  filtersTest("selecting a preset narrows the list to matching exchanges only", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/payload-inspection");
    const rows = alicePage.getByTestId("payload-inspection-row");

    await alicePage.getByTestId("payload-inspection-time-preset-1h").click();
    await expect(rows).toHaveCount(1);
    await expect(rows.first().getByTestId("payload-inspection-row-excerpt")).toContainText(
      "Widgetary Corp"
    );

    await alicePage.getByTestId("payload-inspection-time-preset-today").click();
    await expect(rows).toHaveCount(2);

    await alicePage.getByTestId("payload-inspection-time-preset-all").click();
    await expect(rows).toHaveCount(3);
  });
});

filtersTest.describe("Payload inspection — per-hour histogram", () => {
  filtersTest("lists one row per hour that holds a retained exchange, and clicking one narrows the list", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/payload-inspection");
    const bars = alicePage.getByTestId("payload-inspection-histogram-hour");
    // Three distinct hour buckets: "recent" and "hour_ago" are 65 minutes
    // apart (always crosses an hour boundary), "yesterday" is a different day.
    await expect(bars).toHaveCount(3);
    for (let i = 0; i < 3; i++) {
      await expect(bars.nth(i).getByTestId("payload-inspection-histogram-hour-count")).toHaveText(
        "1"
      );
    }

    // Clicking the oldest (yesterday's) bucket narrows the list to just that
    // exchange.
    await bars.first().click();
    const rows = alicePage.getByTestId("payload-inspection-row");
    await expect(rows).toHaveCount(1);
    await expect(rows.first().getByTestId("payload-inspection-row-excerpt")).toContainText(
      "Solstice Analytics"
    );
    await expect(bars.first()).toHaveAttribute("aria-pressed", "true");
  });
});

filtersTest.describe("Payload inspection — filters combine (AND) and round-trip through the URL", () => {
  filtersTest("outcome + time preset combine, and reloading the URL restores the same filtered view", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/payload-inspection");
    const rows = alicePage.getByTestId("payload-inspection-row");

    await alicePage.getByTestId("payload-inspection-time-preset-today").click();
    await alicePage.getByTestId("payload-inspection-outcome-filter-never_sent").click();
    await expect(rows).toHaveCount(1);
    await expect(rows.first().getByTestId("payload-inspection-row-excerpt")).toContainText(
      "Nightshade Ventures"
    );

    await expect(alicePage).toHaveURL(/time=today/);
    await expect(alicePage).toHaveURL(/outcome=never_sent/);

    await alicePage.reload();
    await expect(alicePage.getByTestId("payload-inspection-row")).toHaveCount(1);
    await expect(alicePage.getByTestId("payload-inspection-outcome-filter-never_sent")).toHaveAttribute(
      "aria-selected",
      "true"
    );
    await expect(alicePage.getByTestId("payload-inspection-time-preset-today")).toHaveAttribute(
      "aria-pressed",
      "true"
    );
  });

  filtersTest("the URL never carries a real value -- only the blindfolded search term typed in", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/payload-inspection");
    await alicePage.getByTestId("payload-inspection-search").fill("Widgetary");
    await expect(alicePage).toHaveURL(/q=Widgetary/);
    await expect(alicePage.getByTestId("payload-inspection-row")).toHaveCount(1);

    await alicePage.reload();
    await expect(alicePage.getByTestId("payload-inspection-search")).toHaveValue("Widgetary");
    await expect(alicePage.getByTestId("payload-inspection-row")).toHaveCount(1);
  });
});

filtersTest.describe("Payload inspection — no calendar-range controls", () => {
  filtersTest("no month presets and no from/to date inputs exist anywhere on the page", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/payload-inspection");
    await expect(alicePage.locator('input[type="date"]')).toHaveCount(0);
    for (const forbidden of ["Current month", "Previous month", "From date", "To date"]) {
      await expect(alicePage.getByTestId("payload-inspection-filters")).not.toContainText(forbidden);
    }
  });
});
