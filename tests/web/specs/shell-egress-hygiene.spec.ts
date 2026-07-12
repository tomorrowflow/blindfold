import { test, expect } from "./fixtures";

// Egress hygiene for the new shell (ADR-0026, issue #93): unlike the legacy
// org-graph page (which still loads Cytoscape from a CDN, see
// egress-hygiene.spec.ts — retired separately), the vendored bundle must make
// ZERO requests to any non-loopback origin. Fonts (IBM Plex, @fontsource) and
// icons (lucide-react) are bundled at build time, not fetched from a CDN.

const SHELL_ROUTES = [
  "/ui/",
  "/ui/status",
  "/ui/entities",
  "/ui/graph",
  "/ui/inbox",
  "/ui/audit",
];

test("the shell makes zero requests to a non-loopback origin", async ({ page, baseURL }) => {
  const requestHosts = new Set<string>();
  page.on("request", (req) => requestHosts.add(new URL(req.url()).host));

  for (const route of SHELL_ROUTES) {
    await page.goto(route);
    await expect(page.locator("nav.bf-sidebar")).toBeVisible();
  }

  const firstPartyHost = new URL(baseURL!).host;
  const thirdParty = [...requestHosts].filter((host) => host !== firstPartyHost);

  expect(thirdParty, `unexpected non-loopback requests: ${thirdParty.join(", ")}`).toEqual([]);
});
