import { test as base, expect, request as pwRequest, type Page } from "@playwright/test";

export const WORKSPACE = "acme";
export const REAL_PERSON = "Martin Bach";
export const PERSON_SURROGATE = "Clara Hoffmann";
export const REAL_ORG = "Initech GmbH";
export const ORG_SURROGATE = "Pinnacle Corp";

type Fixtures = {
  alicePage: Page; // holds re-identifier + viewer on WORKSPACE
  bobPage: Page; // holds no role on WORKSPACE (unauthorized)
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
