import { test as base, expect, request as pwRequest } from "@playwright/test";
import { REAL_PERSON, REAL_ORG, WORKSPACE, auditEventsFor } from "./fixtures";

// Payload inspection (issue #432, ADR-0059 amendment #431 §7): its own
// primary-nav destination -- a retained-exchange list on the left, the
// selected exchange's elided diff / replacements-first table / exchange-level
// bulk Reveal switch / leak-gate verdict on the right. Formerly the Processing
// trace's own fourth grain level (issue #400/#401/#403); this spec replaces
// processing-trace-retained-payload.spec.ts, which drove the identical
// behaviour through the trace's inline expansion.
//
// Runs against the same two dedicated serve_fixture.py instances
// (playwright.config.ts, BLINDFOLD_FIXTURE_STATE=payload_inspection_retained /
// ..._disarmed) processing-trace-retained-payload.spec.ts used, rather than
// the primary shared instance every other spec file polls -- arming Payload
// inspection process-wide would leak into unrelated specs sharing that same
// server process.
//
// See serve_fixture.py's `_build_payload_inspection_retained_fixture`: the
// retained-exchange STORE (unlike the Processing trace's own ring) holds
// exactly two entries when armed -- "passed, retained" and "blocked,
// retained" -- inserted in that order, so the list's newest-first rendering
// puts "blocked" first, "passed" second. The fixture's other two Processing-
// trace-only rows ("predates arming", "armed but never retained") are never
// pushed into the retained-leaf store at all, so they never appear in this
// list -- they exist only to give processing-trace.spec.ts's own link-or-
// nothing assertions a non-retained row to check against.

const RETAINED_BASE_URL = "http://127.0.0.1:8959";
const DISARMED_BASE_URL = "http://127.0.0.1:8960";

function fixtureTest(baseURL: string) {
  return base.extend<{
    alicePage: import("@playwright/test").Page;
    erinPage: import("@playwright/test").Page; // viewer only, no re-identifier (issue #401)
  }>({
    alicePage: async ({ browser }, use) => {
      const context = await browser.newContext({
        baseURL,
        extraHTTPHeaders: { "x-blindfold-identity": "alice" },
      });
      const page = await context.newPage();
      await use(page);
      await context.close();
    },
    erinPage: async ({ browser }, use) => {
      const context = await browser.newContext({
        baseURL,
        extraHTTPHeaders: { "x-blindfold-identity": "erin" },
      });
      const page = await context.newPage();
      await use(page);
      await context.close();
    },
  });
}

const retainedTest = fixtureTest(RETAINED_BASE_URL);
const disarmedTest = fixtureTest(DISARMED_BASE_URL);

retainedTest.describe("Payload inspection — exchange list", () => {
  retainedTest("lists every retained exchange, newest first, with its own metadata", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/payload-inspection");
    const rows = alicePage.getByTestId("payload-inspection-row");
    await expect(rows).toHaveCount(2);
    await expect(alicePage.getByTestId("payload-inspection-count")).toContainText("2 exchanges");

    // Newest first: "blocked, retained" (ts ...21) before "passed, retained" (ts ...20).
    await expect(rows.nth(0).getByTestId("payload-inspection-row-outcome")).toHaveText(
      "Never sent"
    );
    await expect(rows.nth(1).getByTestId("payload-inspection-row-outcome")).toHaveText("Sent");

    // Replacement counts: blocked exchange has 1 span, passed exchange has 6
    // (2 + 2 overlapping + 2) across its three leaves.
    await expect(rows.nth(0).getByTestId("payload-inspection-row-count")).toHaveText("1");
    await expect(rows.nth(1).getByTestId("payload-inspection-row-count")).toHaveText("6");

    // The excerpt is the first retained leaf's own blindfolded text -- never a
    // real value, and never the SECOND leaf's text.
    await expect(rows.nth(1).getByTestId("payload-inspection-row-excerpt")).toContainText(
      "Clara Hoffmann"
    );
    await expect(rows.nth(1).getByTestId("payload-inspection-row-excerpt")).not.toContainText(
      REAL_PERSON
    );
  });

  retainedTest("selecting a row opens it and resets Reveal to blindfolded", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/payload-inspection");
    const rows = alicePage.getByTestId("payload-inspection-row");

    // Default selection is the newest exchange (blocked, "never sent").
    await expect(alicePage.getByTestId("retained-payload-never-sent")).toBeVisible();

    // Reveal the passed exchange's confirmed surrogates, then switch away --
    // the new selection must render blindfolded again, not carry the reveal
    // over from the previously selected exchange.
    await rows.nth(1).click();
    await expect(alicePage.getByTestId("retained-payload-never-sent")).toHaveCount(0);
    await alicePage.getByTestId("retained-payload-reveal-switch").click();
    await expect(alicePage.getByTestId("retained-leaf-span-revealed").first()).toBeVisible();

    await rows.nth(0).click();
    await expect(alicePage.getByTestId("retained-payload-never-sent")).toBeVisible();
    await rows.nth(1).click();
    await expect(alicePage.getByTestId("retained-payload-reveal-switch")).toHaveAttribute(
      "aria-checked",
      "false"
    );
    await expect(alicePage.getByTestId("retained-leaf-span-revealed")).toHaveCount(0);
  });
});

// Issue #432's other own half: the Processing trace stops embedding retained
// leaves and instead links a retained row to Payload inspection's own
// destination. Uses the retained fixture's full 4-row trace (predates /
// passed / blocked / evicted, newest-first after reversal: evicted, blocked,
// passed, predates) -- only "passed" and "blocked" are actually in the
// retained-leaf store, so only those two rows get a link.
retainedTest.describe("Processing trace — links to Payload inspection instead of embedding it", () => {
  retainedTest("a retained row's Payload cell links to the exchange; a non-retained row's does not", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/processing-trace");
    const rows = alicePage.getByTestId("processing-trace-row");
    await expect(rows).toHaveCount(4);

    await expect(rows.nth(0).getByTestId("processing-trace-row-retained-link")).toHaveCount(0); // evicted
    await expect(rows.nth(1).getByTestId("processing-trace-row-retained-link")).toHaveText(
      "View →"
    ); // blocked, retained
    await expect(rows.nth(2).getByTestId("processing-trace-row-retained-link")).toHaveText(
      "View →"
    ); // passed, retained
    await expect(rows.nth(3).getByTestId("processing-trace-row-retained-link")).toHaveCount(0); // predates arming

    // The trace no longer renders the leaves themselves -- expanding a row's
    // hops never surfaces a "Retained payload" section.
    await rows.nth(2).click();
    await expect(alicePage.getByTestId("processing-trace-hop-cards")).toBeVisible();
    await expect(alicePage.getByTestId("retained-payload-section")).toHaveCount(0);

    // The link navigates to Payload inspection with that exact exchange selected.
    await rows.nth(1).getByTestId("processing-trace-row-retained-link").click();
    await expect(alicePage).toHaveURL(/\/ui\/payload-inspection\?exchange=/);
    await expect(alicePage.getByTestId("retained-payload-section").getByTestId(
      "retained-payload-never-sent"
    )).toBeVisible();
  });
});

retainedTest.describe("Payload inspection — retained payload (armed)", () => {
  retainedTest(
    "a retained passed exchange renders its leaves as an elided diff with spans marked",
    async ({ alicePage }) => {
      await alicePage.goto("/ui/payload-inspection");
      const rows = alicePage.getByTestId("payload-inspection-row");
      await rows.nth(1).click();

      const section = alicePage.getByTestId("retained-payload-section");
      await expect(section).toContainText("Retained payload");
      const cards = section.getByTestId("retained-leaf-card");
      // 3rd leaf (issue #401) carries this exchange's `pending`/`rejected`
      // surrogates, alongside these first two leaves' `confirmed` ones.
      await expect(cards).toHaveCount(3);
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
      await alicePage.goto("/ui/payload-inspection");
      const rows = alicePage.getByTestId("payload-inspection-row");
      await rows.nth(1).click();
      const section = alicePage.getByTestId("retained-payload-section");
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
    await alicePage.goto("/ui/payload-inspection");
    // Blocked exchange is the default (newest-first) selection.
    const section = alicePage.getByTestId("retained-payload-section");
    await expect(section.getByTestId("retained-payload-never-sent")).toContainText("Never sent");
    await expect(section.getByTestId("retained-leaf-card")).toHaveCount(1);
    await expect(section.getByTestId("retained-leaf-span")).toHaveText("Clara Hoffmann");
  });

  retainedTest("the leak gate's own verdict is shown, and the view states it does not detect leaks", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/payload-inspection");
    const rows = alicePage.getByTestId("payload-inspection-row");
    await rows.nth(1).click();
    const section = alicePage.getByTestId("retained-payload-section");
    await expect(section.getByTestId("retained-payload-leak-verdict")).toHaveText("passed");
    await expect(section.getByTestId("retained-payload-claim")).toContainText("not a leak check");
  });
});

retainedTest.describe("Payload inspection — replacements-first table view", () => {
  retainedTest(
    "a view toggle switches between the elided diff and the table, defaulting to diff",
    async ({ alicePage }) => {
      await alicePage.goto("/ui/payload-inspection");
      const rows = alicePage.getByTestId("payload-inspection-row");
      await rows.nth(1).click();
      const section = alicePage.getByTestId("retained-payload-section");

      const toggle = section.getByTestId("retained-payload-view-toggle");
      await expect(toggle).toBeVisible();
      const diffButton = section.getByTestId("retained-payload-view-diff-button");
      const tableButton = section.getByTestId("retained-payload-view-table-button");
      await expect(diffButton).toHaveAttribute("aria-selected", "true");
      await expect(tableButton).toHaveAttribute("aria-selected", "false");

      await expect(section.getByTestId("retained-leaf-card")).toHaveCount(3);
      await expect(section.getByTestId("retained-payload-table")).toHaveCount(0);
    }
  );

  retainedTest(
    "the table shows one row per substitution, across more than one leaf kind, overlapping spans included",
    async ({ alicePage }) => {
      await alicePage.goto("/ui/payload-inspection");
      const rows = alicePage.getByTestId("payload-inspection-row");
      await rows.nth(1).click();
      const section = alicePage.getByTestId("retained-payload-section");

      await section.getByTestId("retained-payload-view-table-button").click();
      await expect(section.getByTestId("retained-leaf-card")).toHaveCount(0);

      const table = section.getByTestId("retained-payload-table");
      await expect(table).toBeVisible();
      const headers = table.locator("th");
      await expect(headers).toHaveText(["Value", "Surrogate", "Lifecycle", "Context", "Leaf"]);

      const dataRows = table.getByTestId("retained-payload-table-row");
      await expect(dataRows).toHaveCount(6);

      await expect(dataRows.nth(0).getByTestId("retained-payload-table-value")).toHaveText(
        "Clara Hoffmann"
      );
      await expect(dataRows.nth(0).getByTestId("retained-payload-table-lifecycle")).toHaveText(
        "confirmed"
      );
      await expect(dataRows.nth(4).getByTestId("retained-payload-table-lifecycle")).toHaveText(
        "pending"
      );
      await expect(dataRows.nth(5).getByTestId("retained-payload-table-lifecycle")).toHaveText(
        "rejected"
      );

      const bodyText = await section.innerText();
      expect(bodyText).not.toContain(REAL_PERSON);
      expect(bodyText).not.toContain(REAL_ORG);
    }
  );

  retainedTest(
    "flipping the shared Reveal switch resolves confirmed rows' Value column; pending/rejected stay unresolved",
    async ({ alicePage }) => {
      const before = await auditEventsFor(RETAINED_BASE_URL, "re-identified", "alice");
      await alicePage.goto("/ui/payload-inspection");
      const rows = alicePage.getByTestId("payload-inspection-row");
      await rows.nth(1).click();
      const section = alicePage.getByTestId("retained-payload-section");
      await section.getByTestId("retained-payload-view-table-button").click();

      const dataRows = section.getByTestId("retained-payload-table-row");
      await section.getByTestId("retained-payload-reveal-switch").click();
      await expect(dataRows.nth(0).getByTestId("retained-payload-table-value")).toHaveText(
        REAL_PERSON
      );
      await expect(dataRows.nth(1).getByTestId("retained-payload-table-value")).toHaveText(
        REAL_ORG
      );
      await expect(dataRows.nth(4).getByTestId("retained-payload-table-value")).not.toHaveText(
        REAL_PERSON
      );

      const after = await auditEventsFor(RETAINED_BASE_URL, "re-identified", "alice");
      expect(after.length).toBe(before.length + 1);
    }
  );
});

retainedTest.describe("Payload inspection — exchange-level bulk Reveal switch", () => {
  retainedTest(
    "defaults to blindfolded, flips every confirmed span in the exchange at once, and reverts on reselect",
    async ({ alicePage }) => {
      await alicePage.goto("/ui/payload-inspection");
      const rows = alicePage.getByTestId("payload-inspection-row");
      await rows.nth(1).click();
      const section = alicePage.getByTestId("retained-payload-section");
      const cards = section.getByTestId("retained-leaf-card");

      const revealSwitch = section.getByTestId("retained-payload-reveal-switch");
      await expect(revealSwitch).toHaveAttribute("aria-checked", "false");
      await expect(cards.nth(0)).toContainText("Clara Hoffmann");
      await expect(cards.nth(0)).not.toContainText(REAL_PERSON);
      await expect(section.getByTestId("retained-leaf-span-revealed")).toHaveCount(0);

      await revealSwitch.click();
      await expect(revealSwitch).toHaveAttribute("aria-checked", "true");
      await expect(cards.nth(0)).toContainText(REAL_PERSON);
      await expect(cards.nth(0)).toContainText(REAL_ORG);
      await expect(cards.nth(0)).not.toContainText("Clara Hoffmann");

      const pendingRejectedCard = cards.nth(2);
      await expect(pendingRejectedCard.getByTestId("retained-leaf-span-revealed")).toHaveCount(0);
      await expect(pendingRejectedCard.getByTestId("retained-leaf-span")).toHaveCount(2);

      // Reselecting the same row (issue #432's own "resets Reveal to
      // blindfolded" bar) reverts -- no persistence.
      await rows.nth(0).click();
      await rows.nth(1).click();
      const reopenedSection = alicePage.getByTestId("retained-payload-section");
      await expect(reopenedSection.getByTestId("retained-payload-reveal-switch")).toHaveAttribute(
        "aria-checked",
        "false"
      );
      await expect(reopenedSection.getByTestId("retained-leaf-card").nth(0)).not.toContainText(
        REAL_PERSON
      );
    }
  );

  retainedTest(
    "flipping writes exactly one audit event, whatever the number of confirmed surrogates",
    async ({ alicePage }) => {
      const before = await auditEventsFor(RETAINED_BASE_URL, "re-identified", "alice");
      await alicePage.goto("/ui/payload-inspection");
      const rows = alicePage.getByTestId("payload-inspection-row");
      await rows.nth(1).click();
      const section = alicePage.getByTestId("retained-payload-section");
      await section.getByTestId("retained-payload-reveal-switch").click();
      await expect(section.getByTestId("retained-leaf-span-revealed").first()).toBeVisible();
      const after = await auditEventsFor(RETAINED_BASE_URL, "re-identified", "alice");
      expect(after.length).toBe(before.length + 1);
    }
  );

  retainedTest(
    "without re-identifier the switch is visible but styled locked, and an attempt writes a denied audit event",
    async ({ erinPage }) => {
      const before = await auditEventsFor(RETAINED_BASE_URL, "re-identify-denied", "erin");
      await erinPage.goto("/ui/payload-inspection");
      const rows = erinPage.getByTestId("payload-inspection-row");
      await rows.nth(1).click();
      const section = erinPage.getByTestId("retained-payload-section");
      const revealSwitch = section.getByTestId("retained-payload-reveal-switch");

      await expect(revealSwitch).toBeVisible();
      await expect(revealSwitch).toBeEnabled();

      await revealSwitch.click();
      await expect(section.getByTestId("retained-payload-reveal-denied")).toBeVisible();
      await expect(revealSwitch).toHaveAttribute("aria-checked", "false");

      const after = await auditEventsFor(RETAINED_BASE_URL, "re-identify-denied", "erin");
      expect(after.length).toBe(before.length + 1);

      const bodyText = await erinPage.locator("body").innerText();
      expect(bodyText).not.toContain(REAL_PERSON);
      expect(bodyText).not.toContain(REAL_ORG);
    }
  );

  // Carried over from processing-trace-retained-payload.spec.ts (issue #401):
  // the reveal switch's own request is the one call on this page that returns
  // real values over the wire (ADR-0059 §5), so its first-party claim is
  // asserted directly rather than left to the list-driving egress test below.
  retainedTest(
    "resolving does not consult the review inbox, and the bulk-resolve request itself stays first-party",
    async ({ alicePage }) => {
      const requests: string[] = [];
      alicePage.on("request", (req) => requests.push(req.url()));

      await alicePage.goto("/ui/payload-inspection");
      const rows = alicePage.getByTestId("payload-inspection-row");
      await rows.nth(1).click();
      const section = alicePage.getByTestId("retained-payload-section");
      await section.getByTestId("retained-payload-reveal-switch").click();
      await expect(section.getByTestId("retained-leaf-span-revealed").first()).toBeVisible();

      expect(requests.some((url) => url.includes("/review-inbox"))).toBe(false);

      const firstPartyOrigin = new URL(RETAINED_BASE_URL).host;
      const thirdParty = requests.filter((url) => {
        try {
          return new URL(url).host !== firstPartyOrigin;
        } catch {
          return false;
        }
      });
      expect(
        thirdParty,
        "expected zero third-party requests while resolving the bulk Reveal switch"
      ).toEqual([]);

      const reidentifyRequests = requests.filter((url) => url.includes("/surrogate/") && url.includes("/real"));
      expect(reidentifyRequests.length).toBeGreaterThan(0);
      for (const url of reidentifyRequests) {
        expect(new URL(url).host).toBe(firstPartyOrigin);
      }
    }
  );

  // Carried over from processing-trace-retained-payload.spec.ts (issue #403):
  // the same privacy properties, driven from the table view rather than the diff.
  retainedTest(
    "egress hygiene holds in the table view too: switching to Table and revealing stays first-party",
    async ({ alicePage }) => {
      const requests: string[] = [];
      alicePage.on("request", (req) => requests.push(req.url()));

      await alicePage.goto("/ui/payload-inspection");
      const rows = alicePage.getByTestId("payload-inspection-row");
      await rows.nth(1).click();
      const section = alicePage.getByTestId("retained-payload-section");
      await section.getByTestId("retained-payload-view-table-button").click();
      await section.getByTestId("retained-payload-reveal-switch").click();
      await expect(
        section
          .getByTestId("retained-payload-table-row")
          .first()
          .getByTestId("retained-payload-table-value")
      ).toHaveText(REAL_PERSON);

      const firstPartyOrigin = new URL(RETAINED_BASE_URL).host;
      const thirdParty = requests.filter((url) => {
        try {
          return new URL(url).host !== firstPartyOrigin;
        } catch {
          return false;
        }
      });
      expect(
        thirdParty,
        "expected zero third-party requests while driving the table view's reveal switch"
      ).toEqual([]);
    }
  );

  retainedTest(
    "a denied reveal attempt in the table view is audited as denied, and its Value column never resolves",
    async ({ erinPage }) => {
      const before = await auditEventsFor(RETAINED_BASE_URL, "re-identify-denied", "erin");
      await erinPage.goto("/ui/payload-inspection");
      const rows = erinPage.getByTestId("payload-inspection-row");
      await rows.nth(1).click();
      const section = erinPage.getByTestId("retained-payload-section");
      await section.getByTestId("retained-payload-view-table-button").click();
      await section.getByTestId("retained-payload-reveal-switch").click();
      await expect(section.getByTestId("retained-payload-reveal-denied")).toBeVisible();

      const after = await auditEventsFor(RETAINED_BASE_URL, "re-identify-denied", "erin");
      expect(after.length).toBe(before.length + 1);

      const bodyText = await erinPage.locator("body").innerText();
      expect(bodyText).not.toContain(REAL_PERSON);
      expect(bodyText).not.toContain(REAL_ORG);
    }
  );
});

disarmedTest.describe("Payload inspection — disarmed", () => {
  disarmedTest("the list is empty and names disarmed, with a link to where it's armed", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/payload-inspection");
    await expect(alicePage.getByTestId("payload-inspection-row")).toHaveCount(0);
    const disarmed = alicePage.getByTestId("payload-inspection-disarmed");
    await expect(disarmed).toContainText("disarmed");
    const link = disarmed.getByTestId("payload-inspection-arm-link");
    await expect(link).toHaveText("Arm it in Settings →");
    await link.click();
    await expect(alicePage).toHaveURL(/\/ui\/settings$/);
    await expect(alicePage.getByTestId("payload-inspection-arm-toggle")).toBeVisible();
  });
});

// Unprotected mode active (ADR-0059 §4: "nothing is retained while Unprotected
// mode is active") is a THIRD distinguishable empty state, distinct from
// "disarmed" and from "armed but nothing retained yet" -- issue #432's own AC.
// There is no SPA control that activates Unprotected mode itself (only its
// capability toggle, Settings -> Unprotected mode; activation is menu-bar-app
// only, ADR-0038), so this drives the proxy's own unauthenticated loopback
// endpoints directly, on the disarmed fixture's otherwise-unused store (a
// genuinely empty one -- arming it here adds no exchange, since nothing is
// forwarded through the request path during this test).
disarmedTest.describe("Payload inspection — Unprotected mode active", () => {
  disarmedTest(
    "an armed-but-empty list under an active Unprotected-mode override reads as Unprotected, not merely empty",
    async ({ alicePage }) => {
      const api = await pwRequest.newContext({ baseURL: DISARMED_BASE_URL });
      await api.post(`/v1/management/payload-inspection?workspace=${WORKSPACE}`, {
        headers: { "x-blindfold-identity": "alice" },
      });
      await api.post("/v1/unprotected-mode/capability", { data: { enabled: true } });
      await api.post("/v1/unprotected-mode", { data: { bound: "infinite" } });

      try {
        await alicePage.goto("/ui/payload-inspection");
        await expect(alicePage.getByTestId("payload-inspection-row")).toHaveCount(0);
        await expect(alicePage.getByTestId("payload-inspection-unprotected-active")).toContainText(
          "Unprotected mode is active"
        );
        await expect(alicePage.getByTestId("payload-inspection-disarmed")).toHaveCount(0);
      } finally {
        // Leave the fixture as this file found it -- other tests in this
        // describe block share the same long-lived serve_fixture.py process.
        await api.delete("/v1/unprotected-mode");
        await api.delete(`/v1/management/payload-inspection?workspace=${WORKSPACE}`, {
          headers: { "x-blindfold-identity": "alice" },
        });
        await api.dispose();
      }
    }
  );
});

// SPA-side privacy properties for this slice (browser-verify gate, issue #432,
// carried over unchanged in substance from issue #400/#401's own coverage of
// the same rendering, now reached via Payload inspection's own destination).
retainedTest.describe("Payload inspection — SPA-side privacy properties", () => {
  retainedTest(
    "egress hygiene: selecting every row and every elision stays first-party, never a third-party origin",
    async ({ alicePage }) => {
      const requests: { url: string; postData: string | null }[] = [];
      alicePage.on("request", (req) => {
        requests.push({ url: req.url(), postData: req.postData() });
      });

      await alicePage.goto("/ui/payload-inspection");
      const rows = alicePage.getByTestId("payload-inspection-row");
      const rowCount = await rows.count();
      for (let i = 0; i < rowCount; i++) {
        await rows.nth(i).click();
        const elisions = alicePage.getByTestId("retained-leaf-elision");
        while ((await elisions.count()) > 0) {
          await elisions.first().click();
        }
      }

      const firstPartyOrigin = new URL(RETAINED_BASE_URL).host;
      const thirdParty = requests.filter((r) => {
        try {
          return new URL(r.url).host !== firstPartyOrigin;
        } catch {
          return false;
        }
      });
      expect(
        thirdParty.map((r) => r.url),
        "expected zero third-party requests while driving Payload inspection"
      ).toEqual([]);

      const leavesRequests = requests.filter((r) => r.url.includes("/payload-inspection/leaves"));
      expect(leavesRequests.length).toBeGreaterThan(0);
      for (const req of leavesRequests) {
        expect(new URL(req.url).host).toBe(firstPartyOrigin);
      }
    }
  );

  retainedTest(
    "no real entity value behind a retained surrogate ever reaches the DOM or the network, only its blindfolded form",
    async ({ alicePage }) => {
      const responseBodies: string[] = [];
      alicePage.on("response", async (res) => {
        if (res.url().includes("/v1/management/")) {
          try {
            responseBodies.push(await res.text());
          } catch {
            // ignore bodies that can't be read (e.g. aborted by navigation)
          }
        }
      });

      await alicePage.goto("/ui/payload-inspection");
      const rows = alicePage.getByTestId("payload-inspection-row");
      const rowCount = await rows.count();
      for (let i = 0; i < rowCount; i++) {
        await rows.nth(i).click();
        const elisions = alicePage.getByTestId("retained-leaf-elision");
        while ((await elisions.count()) > 0) {
          await elisions.first().click();
        }
      }

      const bodyText = await alicePage.locator("body").innerText();
      for (const realValue of [REAL_PERSON, REAL_ORG]) {
        expect(bodyText, `real entity value "${realValue}" leaked into the DOM`).not.toContain(
          realValue
        );
      }
      for (const body of responseBodies) {
        for (const realValue of [REAL_PERSON, REAL_ORG]) {
          expect(
            body,
            `real entity value "${realValue}" leaked into a /v1/management/* response body`
          ).not.toContain(realValue);
        }
      }
    }
  );

  retainedTest(
    "an identity holding no viewer role on the workspace is refused the retained-leaves endpoint outright",
    async ({}) => {
      const api = await pwRequest.newContext({
        baseURL: RETAINED_BASE_URL,
        extraHTTPHeaders: { "x-blindfold-identity": "dave" },
      });
      // dave holds curator only (serve_fixture.py) -- no viewer, so the same
      // `_require_role("viewer")` gate `list_processing_trace` uses must also
      // refuse this endpoint, never fall through to a partial response.
      const res = await api.get(`/v1/management/payload-inspection/leaves?workspace=${WORKSPACE}`);
      expect(res.status()).toBe(403);
      const body = await res.text();
      for (const realValue of [REAL_PERSON, REAL_ORG, "Clara Hoffmann", "Pinnacle Corp"]) {
        expect(body, `refused response still contained "${realValue}"`).not.toContain(realValue);
      }
      await api.dispose();
    }
  );
});
