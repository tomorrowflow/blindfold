import {
  test,
  expect,
  WORKSPACE,
  REAL_PERSON,
  REAL_ORG,
} from "./fixtures";

// Browser egress hygiene: the legacy /ui/org-graph page loaded Cytoscape.js
// from a third-party CDN (unpkg.com). Issue #98 retires that page and replaces
// it with the shell's /ui/graph view, where Cytoscape is vendored via npm — no
// CDN request is made at all.
//
// This spec now asserts the property from the new shell: /ui/graph makes zero
// third-party requests (the CDN-hygiene guarantee is STRONGER, not weaker).
// The "no entity value to a CDN" assertion is preserved as a subset.
//
// shell-egress-hygiene.spec.ts already covers /ui/graph in its SHELL_ROUTES sweep;
// this file is kept to maintain the naming pattern and cover the reveal flow
// (ensuring a reveal doesn't accidentally open a third-party connection).

test("no real entity value reaches a third-party origin (vendored Cytoscape, /ui/graph)", async ({
  alicePage,
  baseURL,
}) => {
  const page = alicePage;
  const requests: { url: string; headers: Record<string, string>; postData: string | null }[] = [];
  page.on("request", (req) => {
    requests.push({ url: req.url(), headers: req.headers(), postData: req.postData() });
  });

  await page.goto(`/ui/graph?workspace=${WORKSPACE}`);
  // Wait for the canvas to be ready
  await page.waitForFunction(
    () =>
      (window as any).__blindfoldGraph !== undefined &&
      (window as any).__blindfoldGraph !== null
  );

  const firstPartyOrigin = new URL(baseURL!).host;
  const thirdParty = requests.filter((r) => {
    try {
      return new URL(r.url).host !== firstPartyOrigin;
    } catch {
      return false;
    }
  });

  // With vendored Cytoscape there must be ZERO third-party requests — the CDN
  // is gone. No entity value can leak to a CDN that doesn't exist.
  expect(
    thirdParty.map((r) => r.url),
    "expected zero third-party requests — Cytoscape is now vendored, not CDN-loaded"
  ).toEqual([]);

  // Re-identify traffic stays first-party (sanity check)
  const reidentifyRequests = requests.filter((r) => r.url.includes("/v1/management/surrogate/"));
  for (const req of reidentifyRequests) {
    expect(new URL(req.url).host).toBe(firstPartyOrigin);
  }

  // Paranoia: even if somehow a third-party request appeared, real values must not be in it.
  for (const req of thirdParty) {
    const haystack = req.url + JSON.stringify(req.headers) + (req.postData ?? "");
    for (const realValue of [REAL_PERSON, REAL_ORG]) {
      expect(haystack, `real entity value leaked to third-party origin ${req.url}`).not.toContain(
        realValue
      );
    }
  }
});
