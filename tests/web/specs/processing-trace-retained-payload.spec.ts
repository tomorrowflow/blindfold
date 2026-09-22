import { test as base, expect, request as pwRequest } from "@playwright/test";
import { REAL_PERSON, REAL_ORG, WORKSPACE, auditEventsFor } from "./fixtures";

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

      // A real multi-hop exchange (issue #400's own bar), not a hopless stub --
      // the retained-payload section renders alongside genuine hop cards.
      const hopCards = alicePage.getByTestId("processing-trace-hop-card");
      await expect(hopCards).toHaveCount(2);
      await expect(hopCards.nth(0)).toContainText("system");
      await expect(hopCards.nth(1)).toContainText("user");

      const section = alicePage.getByTestId("retained-payload-section").nth(0);
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

// SPA-side privacy properties for this slice (browser-verify gate, issue #400).
//
// - Browser egress hygiene: applicable, and asserted below on the armed fixture
//   (the only one that actually carries retained leaf bytes over the wire).
// - Authorized-only re-identification: retained-leaf `text` is blindfolded-form
//   ONLY by construction (ADR-0059 §2 — RewrittenLeaf never carries a real
//   value; the accumulator that builds it runs before any encrypt/decrypt seam
//   exists). There is no decrypt affordance anywhere in RetainedLeafCard or
//   RetainedPayloadSection, so "authorized-only re-identification" has no
//   re-identification action to gate on the new UI surface itself. What IS
//   this slice's responsibility is that the endpoint backing that surface
//   stays viewer-gated for an identity that lacks the role — asserted below
//   directly against the armed fixture (the one instance where a gating bug
//   would actually leak retained bytes, not just an empty list). The
//   pre-existing `re-identifier`-gated Reveal control elsewhere on this same
//   page (hop-injected surrogates, ADR-0035) is unchanged by this diff and
//   already covered by processing-trace.spec.ts — not re-asserted here.
// - Audit-on-decrypt: N/A for this slice. Viewing a retained leaf triggers no
//   decrypt (there is nothing to decrypt), so there is no reveal action here
//   that could go unaudited. Arm/disarm's own audit records
//   (payload-inspection-armed / -arm-refused) predate this diff and are
//   covered by settings-payload-inspection*.spec.ts.
retainedTest.describe("Processing trace — retained payload SPA-side privacy properties", () => {
  retainedTest(
    "egress hygiene: expanding every row and every elision stays first-party, never a third-party origin",
    async ({ alicePage }) => {
      const requests: { url: string; postData: string | null }[] = [];
      alicePage.on("request", (req) => {
        requests.push({ url: req.url(), postData: req.postData() });
      });

      await alicePage.goto("/ui/processing-trace");
      const rows = alicePage.getByTestId("processing-trace-row");
      for (let i = 0; i < 4; i++) {
        await rows.nth(i).click();
      }
      // Expand every collapsed elision too — the fullest possible render of
      // retained leaf text this page can produce. Each click replaces that
      // button with an expanded <span>, shrinking the collection, so always
      // take the first remaining one rather than indexing by a fixed count.
      const elisions = alicePage.getByTestId("retained-leaf-elision");
      while ((await elisions.count()) > 0) {
        await elisions.first().click();
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
        "expected zero third-party requests while driving the retained-payload section"
      ).toEqual([]);

      // The retained-leaves fetch itself must be first-party too, not just
      // "no third party happened to fire" — a stray CDN/analytics beacon on
      // this exact page would otherwise slip past the check above unnoticed.
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

      await alicePage.goto("/ui/processing-trace");
      const rows = alicePage.getByTestId("processing-trace-row");
      for (let i = 0; i < 4; i++) {
        await rows.nth(i).click();
      }
      const elisions = alicePage.getByTestId("retained-leaf-elision");
      while ((await elisions.count()) > 0) {
        await elisions.first().click();
      }

      // PERSON_SURROGATE ("Clara Hoffmann") is the retained leaves' own text —
      // it is also the fixture's real re-identifiable surrogate for
      // REAL_PERSON ("Martin Bach"), so this is a genuine "would the real
      // value behind this exact surrogate ever surface here" check, not a
      // fixture value chosen at random.
      const bodyText = await alicePage.locator("body").innerText();
      for (const realValue of [REAL_PERSON, REAL_ORG]) {
        expect(bodyText, `real entity value "${realValue}" leaked into the retained-payload DOM`).not.toContain(
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
      // dave holds curator only (serve_fixture.py) — no viewer, so the same
      // `_require_role("viewer")` gate `list_processing_trace` uses must also
      // refuse this new endpoint, never fall through to a partial response.
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

// Exchange-level bulk Reveal switch (issue #401, ADR-0059 §5): one switch per
// exchange, defaulting to blindfolded, resolving `confirmed` surrogates only,
// exactly one audit event per flip, `re-identifier`-gated but present (not
// hidden) for a caller without the role, and reverting on collapse. All
// against the "passed, retained" row (rows.nth(2)) -- the ONE exchange the
// fixture gives all three reveal lifecycles side by side (leaf-0: confirmed
// "Clara Hoffmann"/"Pinnacle Corp"; leaf-2: pending + rejected), issue #401's
// own verification bar.
retainedTest.describe("Processing trace — exchange-level bulk Reveal switch", () => {
  retainedTest(
    "defaults to blindfolded, flips every confirmed span in the exchange at once, and reverts on collapse",
    async ({ alicePage }) => {
      await alicePage.goto("/ui/processing-trace");
      const rows = alicePage.getByTestId("processing-trace-row");
      await rows.nth(2).click();
      const section = alicePage.getByTestId("retained-payload-section").nth(0);
      const cards = section.getByTestId("retained-leaf-card");

      // Blindfolded by default: the confirmed spans still read as surrogates.
      const revealSwitch = section.getByTestId("retained-payload-reveal-switch");
      await expect(revealSwitch).toHaveAttribute("aria-checked", "false");
      await expect(cards.nth(0)).toContainText("Clara Hoffmann");
      await expect(cards.nth(0)).not.toContainText(REAL_PERSON);
      await expect(cards.nth(0)).not.toContainText(REAL_ORG);
      await expect(section.getByTestId("retained-leaf-span-revealed")).toHaveCount(0);

      await revealSwitch.click();
      await expect(revealSwitch).toHaveAttribute("aria-checked", "true");

      // Every expanded leaf's confirmed span resolves at once, not just one.
      await expect(cards.nth(0)).toContainText(REAL_PERSON);
      await expect(cards.nth(0)).toContainText(REAL_ORG);
      await expect(cards.nth(0)).not.toContainText("Clara Hoffmann");

      // Pending and rejected stay visibly unresolved -- still their own
      // surrogate token, never a real value, and never marked "revealed".
      const pendingRejectedCard = cards.nth(2);
      const revealedInThatCard = pendingRejectedCard.getByTestId("retained-leaf-span-revealed");
      await expect(revealedInThatCard).toHaveCount(0);
      await expect(pendingRejectedCard.getByTestId("retained-leaf-span")).toHaveCount(2);

      // Collapse the row, then re-expand: the switch reverts (no persistence).
      await rows.nth(2).click();
      await rows.nth(2).click();
      const reopenedSection = alicePage.getByTestId("retained-payload-section").nth(0);
      await expect(reopenedSection.getByTestId("retained-payload-reveal-switch")).toHaveAttribute(
        "aria-checked",
        "false"
      );
      await expect(reopenedSection.getByTestId("retained-leaf-card").nth(0)).toContainText(
        "Clara Hoffmann"
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
      await alicePage.goto("/ui/processing-trace");
      const rows = alicePage.getByTestId("processing-trace-row");
      await rows.nth(2).click();
      const section = alicePage.getByTestId("retained-payload-section").nth(0);
      // Two confirmed surrogates in this exchange (Clara Hoffmann, Pinnacle
      // Corp) -- one flip must still be exactly one audit record, not two.
      await section.getByTestId("retained-payload-reveal-switch").click();
      await expect(section.getByTestId("retained-leaf-span-revealed").first()).toBeVisible();
      const after = await auditEventsFor(RETAINED_BASE_URL, "re-identified", "alice");
      expect(after.length).toBe(before.length + 1);
    }
  );

  retainedTest(
    "the leak gate's own verdict is shown, and the view states it does not detect leaks",
    async ({ alicePage }) => {
      await alicePage.goto("/ui/processing-trace");
      const rows = alicePage.getByTestId("processing-trace-row");
      await rows.nth(2).click();
      const section = alicePage.getByTestId("retained-payload-section").nth(0);
      await expect(section.getByTestId("retained-payload-leak-verdict")).toHaveText("passed");
      await expect(section.getByTestId("retained-payload-claim")).toContainText("not a leak check");
    }
  );

  retainedTest(
    "without re-identifier the switch is visible but styled locked, and an attempt writes a denied audit event",
    async ({ erinPage }) => {
      const before = await auditEventsFor(RETAINED_BASE_URL, "re-identify-denied", "erin");
      await erinPage.goto("/ui/processing-trace");
      const rows = erinPage.getByTestId("processing-trace-row");
      await rows.nth(2).click();
      const section = erinPage.getByTestId("retained-payload-section").nth(0);
      const revealSwitch = section.getByTestId("retained-payload-reveal-switch");

      // Present, not hidden (SEC-8) -- and NOT the native `disabled` state,
      // since a truly disabled control could never be attempted at all.
      await expect(revealSwitch).toBeVisible();
      await expect(revealSwitch).toBeEnabled();

      await revealSwitch.click();
      await expect(section.getByTestId("retained-payload-reveal-denied")).toBeVisible();
      await expect(revealSwitch).toHaveAttribute("aria-checked", "false");

      // The attempt is audited even though it never resolved anything.
      const after = await auditEventsFor(RETAINED_BASE_URL, "re-identify-denied", "erin");
      expect(after.length).toBe(before.length + 1);

      // No real value anywhere on the page, whatever the attempt did server-side.
      const bodyText = await erinPage.locator("body").innerText();
      expect(bodyText).not.toContain(REAL_PERSON);
      expect(bodyText).not.toContain(REAL_ORG);
    }
  );

  retainedTest(
    "resolving does not consult the review inbox",
    async ({ alicePage }) => {
      const requests: string[] = [];
      alicePage.on("request", (req) => requests.push(req.url()));

      await alicePage.goto("/ui/processing-trace");
      const rows = alicePage.getByTestId("processing-trace-row");
      await rows.nth(2).click();
      const section = alicePage.getByTestId("retained-payload-section").nth(0);
      await section.getByTestId("retained-payload-reveal-switch").click();
      await expect(section.getByTestId("retained-leaf-span-revealed").first()).toBeVisible();

      expect(requests.some((url) => url.includes("/review-inbox"))).toBe(false);
    }
  );
});
