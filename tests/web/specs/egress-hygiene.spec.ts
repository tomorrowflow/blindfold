import {
  test,
  expect,
  WORKSPACE,
  REAL_PERSON,
  REAL_ORG,
  PERSON_SURROGATE,
  clickGraphNode,
} from "./fixtures";

// Browser egress hygiene: the org-graph loads Cytoscape.js from a third-party CDN.
// No real (or surrogate) entity value may ever be carried in a request/response to
// that or any other non-first-party origin — re-identify traffic must stay on the
// fixture server's own origin.

test("no real entity value reaches a third-party origin", async ({ alicePage, baseURL }) => {
  const page = alicePage;
  const requests: { url: string; headers: Record<string, string>; postData: string | null }[] =
    [];
  page.on("request", (req) => {
    requests.push({ url: req.url(), headers: req.headers(), postData: req.postData() });
  });

  await page.goto(`/ui/org-graph?workspace=${WORKSPACE}`);
  await clickGraphNode(page, PERSON_SURROGATE);
  await page.locator("#reveal-badge-btn").click();
  await expect(page.locator("#reveal-audit-backdrop")).toHaveClass(/open/);
  await page.locator("#reveal-confirm-yes").click();
  await expect(page.locator("#reveal-badge-btn")).toHaveText(REAL_PERSON);

  const firstPartyOrigin = new URL(baseURL!).host;
  const thirdParty = requests.filter((r) => new URL(r.url).host !== firstPartyOrigin);

  // Sanity: the page really does load a third-party CDN script (Cytoscape) —
  // otherwise the assertion below would be vacuous.
  expect(thirdParty.length).toBeGreaterThan(0);

  for (const req of thirdParty) {
    const haystack = req.url + JSON.stringify(req.headers) + (req.postData ?? "");
    for (const realValue of [REAL_PERSON, REAL_ORG]) {
      expect(haystack, `real entity value leaked to third-party origin ${req.url}`).not.toContain(
        realValue
      );
    }
  }

  // Re-identify traffic itself must stay first-party.
  const reidentifyRequests = requests.filter((r) => r.url.includes("/v1/management/surrogate/"));
  expect(reidentifyRequests.length).toBeGreaterThan(0);
  for (const req of reidentifyRequests) {
    expect(new URL(req.url).host).toBe(firstPartyOrigin);
  }
});
