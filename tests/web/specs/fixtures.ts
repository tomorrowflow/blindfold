import { test as base, expect, request as pwRequest, type Page } from "@playwright/test";

export const WORKSPACE = "acme";
export const REAL_PERSON = "Martin Bach";
export const PERSON_SURROGATE = "Clara Hoffmann";
export const REAL_ORG = "Initech GmbH";
export const ORG_SURROGATE = "Pinnacle Corp";
// Planted duplicate (same real name as REAL_PERSON) + a second term — see
// serve_fixture.py's docstring on PERSON2_SURROGATE/ORG2_SURROGATE (issue #97).
export const PERSON2_SURROGATE = "Devin Novak";
export const REAL_ORG2 = "Initech GmbH Holding";
export const ORG2_SURROGATE = "Meridian Group";
// Graph-editor-shell (#98) exclusive entities: entity-list-shell (#97) must never
// touch these. Alphabetical spec order means entity-list-shell runs first and
// mutates PERSON2/ORG; these new entities stay pristine throughout.
export const PERSON3_SURROGATE = "Jordan Weiss";
export const ORG3_SURROGATE = "Glacier Tech";

type Fixtures = {
  alicePage: Page; // holds re-identifier + viewer + curator + admin on WORKSPACE
  bobPage: Page; // holds no role anywhere (unauthorized; sees no workspace at all)
  davePage: Page; // holds ONLY curator on WORKSPACE — no re-identifier, no admin
};

export const test = base.extend<Fixtures>({
  alicePage: async ({ browser }, use) => {
    const context = await browser.newContext({
      extraHTTPHeaders: { "x-blindfold-identity": "alice" },
    });
    const page = await context.newPage();
    await use(page);
    await context.close();
  },
  bobPage: async ({ browser }, use) => {
    const context = await browser.newContext({
      extraHTTPHeaders: { "x-blindfold-identity": "bob" },
    });
    const page = await context.newPage();
    await use(page);
    await context.close();
  },
  davePage: async ({ browser }, use) => {
    const context = await browser.newContext({
      extraHTTPHeaders: { "x-blindfold-identity": "dave" },
    });
    const page = await context.newPage();
    await use(page);
    await context.close();
  },
});

export { expect };

/** Query the audit log directly as "alice" (holds `viewer`) — independent of the
 * browser page under test, the way a human auditor would inspect the trail. */
export async function auditEventsFor(baseURL: string, event: string, identity: string) {
  const api = await pwRequest.newContext({
    baseURL,
    extraHTTPHeaders: { "x-blindfold-identity": "alice" },
  });
  const res = await api.get(`/v1/management/audit?workspace=${WORKSPACE}`);
  expect(res.ok()).toBeTruthy();
  const body = await res.json();
  await api.dispose();
  return (body.events as Array<{ event: string; identity: string; reason: string }>).filter(
    (r) => r.event === event && r.identity === identity
  );
}

/** Resolve an entity-list row by its CURRENT surrogate label and pin it to a stable
 * `data-testid="entity-row-<id>"` locator (set on the `<tr>` itself). A plain
 * `page.locator("tr", { hasText: surrogate })` re-evaluates on every action; once
 * inline rename replaces the surrogate `<span>` with an `<input>`, the span's text
 * disappears from the row's text content (an input's `value` isn't part of DOM/CSS
 * text content) and the locator stops resolving mid-test. Scoped to `.bf-surrogate-text`
 * so an edge chip elsewhere on the page mentioning the same surrogate (e.g. as
 * someone's employer) never matches a different row (issue #97).
 */
export async function rowByCurrentSurrogate(page: Page, surrogate: string) {
  const testId = await page
    .locator("tr")
    .filter({ has: page.locator(".bf-surrogate-text", { hasText: surrogate }) })
    .getAttribute("data-testid");
  if (!testId) throw new Error(`no entity row found for surrogate ${surrogate}`);
  return page.getByTestId(testId);
}

/** Click a Cytoscape node by its label. The graph renders to a <canvas> with no
 * per-node DOM elements, so we read the node's renderedPosition() via the
 * test-only `window.__blindfoldGraph` hook (spa.py) and issue a real mouse click
 * at that screen coordinate — never bypassing the UI. */
export async function clickGraphNode(page: Page, label: string): Promise<void> {
  await page.waitForFunction(
    () => (window as any).__blindfoldGraph && (window as any).__blindfoldGraph.nodes().length > 0
  );
  const point = await page.evaluate((nodeLabel) => {
    const cy = (window as any).__blindfoldGraph;
    const node = cy.nodes().filter((n: any) => n.data("label") === nodeLabel)[0];
    const rp = node.renderedPosition();
    const rect = document.getElementById("cy")!.getBoundingClientRect();
    return { x: rect.left + rp.x, y: rect.top + rp.y };
  }, label);
  await page.mouse.click(point.x, point.y);
}
