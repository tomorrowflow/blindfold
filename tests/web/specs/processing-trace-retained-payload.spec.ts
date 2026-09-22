import { test as base, expect } from "@playwright/test";

// Processing trace's fourth grain level (ADR-0059 §7, issue #400): expanding a row
// also renders whatever Payload inspection retained for THAT exchange -- an elided
// diff, the leaf's own label, the three distinguishable empty states (disarmed /
// predates-arming / evicted), and the "never sent" mark for a blocked exchange.
//
// Runs against two dedicated serve_fixture.py instances (playwright.config.ts,
// BLINDFOLD_FIXTURE_STATE=payload_inspection_retained / ..._disarmed) rather than
// the primary shared instance every other spec file polls -- arming Payload
// inspection process-wide would leak into unrelated specs sharing that same server
// process. See serve_fixture.py's `_build_payload_inspection_retained_fixture`:
// row order (oldest ts to newest, matching the fixture's own record() calls) is
// "predates arming", "passed, retained", "blocked, retained", "armed but evicted".

const RETAINED_BASE_URL = "http://127.0.0.1:8959";
const DISARMED_BASE_URL = "http://127.0.0.1:8960";

function fixtureTest(baseURL: string) {
  return base.extend<{ alicePage: import("@playwright/test").Page }>({
    alicePage: async ({ browser }, use) => {
      const context = await browser.newContext({
        baseURL,
        extraHTTPHeaders: { "x-blindfold-identity": "alice" },
      });
      const page = await context.newPage();
      await use(page);
      await context.close();
    },
  });
}

const retainedTest = fixtureTest(RETAINED_BASE_URL);
const disarmedTest = fixtureTest(DISARMED_BASE_URL);

retainedTest.describe("Processing trace — retained payload (armed)", () => {
  retainedTest(
    "existing trace behaviour (columns, Live/Paused, outcome pills) is unchanged",
    async ({ alicePage }) => {
      await alicePage.goto("/ui/processing-trace");
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
      await expect(alicePage.getByTestId("processing-trace-row")).toHaveCount(4);
      await expect(alicePage.getByTestId("processing-trace-freshness")).toContainText("polled");
    }
  );

  retainedTest(
    "a retained passed exchange renders its leaves as an elided diff with spans marked",
    async ({ alicePage }) => {
      await alicePage.goto("/ui/processing-trace");
      // Newest-first rendering (ADR-0035 decision 9): row 1 of 4 is the fixture's
      // last-recorded ("evicted"), so the "passed, retained" row is the 3rd from
      // the top (predates < passed < blocked < evicted, reversed).
      const rows = alicePage.getByTestId("processing-trace-row");
      const passedRow = rows.nth(2);
      await passedRow.click();

      const section = alicePage.getByTestId("retained-payload-section").nth(0);
      await expect(section).toContainText("Retained payload");
      const cards = section.getByTestId("retained-leaf-card");
      await expect(cards).toHaveCount(2);
      await expect(cards.nth(0).getByTestId("retained-leaf-label")).toHaveText(
        "user: text block"
      );
      await expect(cards.nth(1).getByTestId("retained-leaf-label")).toHaveText(
        "tool_result: tool-result body"
      );
      // Both rewritten spans are marked in place.
      const spans = cards.nth(0).getByTestId("retained-leaf-span");
      await expect(spans).toHaveCount(2);
      await expect(spans.nth(0)).toHaveText("Clara Hoffmann");
      await expect(spans.nth(1)).toHaveText("Pinnacle Corp");
      // The long run of unchanged text between the two spans collapses into an
      // expandable elision marker instead of rendering in full.
      const elision = cards.nth(0).getByTestId("retained-leaf-elision");
      await expect(elision).toHaveCount(1);
      await expect(elision).toContainText("hidden");
      await expect(cards.nth(0)).not.toContainText("Q3 roadmap timeline");
      await elision.click();
      await expect(cards.nth(0)).toContainText("Q3 roadmap timeline");

      // No "never sent" mark on a passed exchange.
      await expect(section.getByTestId("retained-payload-never-sent")).toHaveCount(0);
    }
  );

  retainedTest(
    "overlapping spans on one leaf are both marked, not merged into a lost span",
    async ({ alicePage }) => {
      await alicePage.goto("/ui/processing-trace");
      const rows = alicePage.getByTestId("processing-trace-row");
      await rows.nth(2).click();
      const section = alicePage.getByTestId("retained-payload-section").nth(0);
      const secondCard = section.getByTestId("retained-leaf-card").nth(1);
      // "Pinnacle Corp" and "Pinnacle Corp Holdings" overlap at the same start
      // offset (ADR-0059 §3) -- the renderer unions them into one highlighted
      // run rather than crashing or dropping either.
      await expect(secondCard).toContainText("Pinnacle Corp Holdings");
      await expect(secondCard.getByTestId("retained-leaf-span").first()).toBeVisible();
    }
  );

  retainedTest("a blocked exchange's retained payload is marked never sent", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/processing-trace");
    const rows = alicePage.getByTestId("processing-trace-row");
    // Blocked row is 2nd from the top (see comment above on ordering).
    await rows.nth(1).click();
    const section = alicePage.getByTestId("retained-payload-section").nth(0);
    await expect(section.getByTestId("retained-payload-never-sent")).toContainText(
      "Never sent"
    );
    await expect(section.getByTestId("retained-leaf-card")).toHaveCount(1);
    await expect(section.getByTestId("retained-leaf-span")).toHaveText("Clara Hoffmann");
  });

  retainedTest(
    "an exchange that predates arming names that reason, distinct from evicted",
    async ({ alicePage }) => {
      await alicePage.goto("/ui/processing-trace");
      const rows = alicePage.getByTestId("processing-trace-row");
      // Oldest row (predates arming) is last, newest-first.
      await rows.nth(3).click();
      const predatesSection = alicePage.getByTestId("retained-payload-section").nth(0);
      await expect(predatesSection.getByTestId("retained-payload-predates-arming")).toContainText(
        "before Payload inspection was armed"
      );
      await expect(predatesSection.getByTestId("retained-payload-evicted")).toHaveCount(0);
      await expect(predatesSection.getByTestId("retained-payload-disarmed")).toHaveCount(0);
    }
  );

  retainedTest("an armed exchange with nothing retained reads as evicted", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/processing-trace");
    const rows = alicePage.getByTestId("processing-trace-row");
    // Newest row (armed, never retained) is first.
    await rows.nth(0).click();
    const evictedSection = alicePage.getByTestId("retained-payload-section").nth(0);
    await expect(evictedSection.getByTestId("retained-payload-evicted")).toContainText(
      "aged out"
    );
    await expect(evictedSection.getByTestId("retained-payload-predates-arming")).toHaveCount(0);
    await expect(evictedSection.getByTestId("retained-payload-disarmed")).toHaveCount(0);
  });
});

disarmedTest.describe("Processing trace — retained payload (disarmed)", () => {
  disarmedTest(
    "every row reads as disarmed and links to where it's armed",
    async ({ alicePage }) => {
      await alicePage.goto("/ui/processing-trace");
      const rows = alicePage.getByTestId("processing-trace-row");
      await expect(rows).toHaveCount(4);
      for (let i = 0; i < 4; i++) {
        await rows.nth(i).click();
      }
      const disarmedStates = alicePage.getByTestId("retained-payload-disarmed");
      await expect(disarmedStates).toHaveCount(4);
      await expect(disarmedStates.first()).toContainText("disarmed");
      const link = disarmedStates.first().getByTestId("retained-payload-arm-link");
      await expect(link).toHaveText("Arm it in Settings →");
      await link.click();
      await expect(alicePage).toHaveURL(/\/ui\/settings$/);
      await expect(alicePage.getByTestId("payload-inspection-arm-toggle")).toBeVisible();
    }
  );
});
