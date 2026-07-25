import { test as base, expect } from "@playwright/test";

// Setup — unencrypted-persistence honesty banner (ADR-0045 §4/§10, issue #227).
//
// Runs against two dedicated `serve_fixture.py` instances (ports 8957/8958, see
// playwright.config.ts): both configure a genuinely *persistent* store
// (`BLINDFOLD_FIXTURE_PERSISTENT_STORE=1`, a fresh per-process scratch SQLite
// file), unlike every other fixture instance in this suite, which stays
// ephemeral. 8957 leaves `BLINDFOLD_OPENBAO_TOKEN` unset (no mapping cipher —
// the banner's shown case); 8958 sets it (the Transit cipher is active — the
// banner's hidden case). Setup.tsx renders the banner only when
// `has_persistent_store && mapping_cipher === "none"` (both conditions), so this
// file is the one place that condition's AND is exercised against a real
// backend, not just read off the diff.
//
// SPA-side privacy properties for this slice (see task brief):
// - Authorized-only re-identification: N/A. Setup renders before any workspace
//   exists (or independent of one) and shows zero entities/surrogates — there is
//   no real value anywhere on this page for an unauthorized viewer to see.
// - Audit-on-decrypt: N/A. The banner is static copy computed from `/v1/status`
//   config fields (booleans/enums describing server *configuration*, never a
//   decrypted value) — no reveal action exists on this page, so nothing is
//   decrypted and nothing should be audited.
// - Browser egress hygiene: applicable and asserted below (zero non-loopback
//   requests) — the general property still holds even though this page never
//   touches entity data.

const UNENCRYPTED_BASE_URL = "http://127.0.0.1:8957"; // persistent store, no mapping cipher
const ENCRYPTED_BASE_URL = "http://127.0.0.1:8958"; // persistent store, Transit cipher configured
const FIXTURE_TOKEN = "fixture-transit-token"; // the fake token playwright.config.ts sets for 8958

type Fixtures = {
  unencryptedPage: import("@playwright/test").Page;
  encryptedPage: import("@playwright/test").Page;
};

const test = base.extend<Fixtures>({
  unencryptedPage: async ({ browser }, use) => {
    const context = await browser.newContext({ baseURL: UNENCRYPTED_BASE_URL });
    const page = await context.newPage();
    await use(page);
    await context.close();
  },
  encryptedPage: async ({ browser }, use) => {
    const context = await browser.newContext({ baseURL: ENCRYPTED_BASE_URL });
    const page = await context.newPage();
    await use(page);
    await context.close();
  },
});

test.describe("Setup — unencrypted-persistence banner shown (persistent store, no cipher)", () => {
  test("the banner renders with the expected copy and env var", async ({ unencryptedPage }) => {
    await unencryptedPage.goto("/ui/setup");
    const banner = unencryptedPage.getByTestId("setup-unencrypted-store-banner");
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("No mapping cipher is configured");
    await expect(banner).toContainText("persisted unencrypted");
    await expect(banner).toContainText("BLINDFOLD_OPENBAO_TOKEN");

    // Distinct condition from "no persistence at all" (setup-shell.spec.ts) --
    // the store here IS persistent, so the ephemeral-store banner must not
    // also render alongside it.
    await expect(
      unencryptedPage.getByTestId("setup-ephemeral-store-banner")
    ).toHaveCount(0);

    // The rest of Setup keeps working normally underneath the banner.
    await expect(unencryptedPage.getByTestId("setup-workspace-name")).toBeVisible();
    await expect(unencryptedPage.getByTestId("setup-create-btn")).toBeVisible();
  });

  test("zero requests to a non-loopback origin", async ({ unencryptedPage }) => {
    const requestHosts = new Set<string>();
    unencryptedPage.on("request", (req) => requestHosts.add(new URL(req.url()).host));

    await unencryptedPage.goto("/ui/setup");
    await expect(
      unencryptedPage.getByTestId("setup-unencrypted-store-banner")
    ).toBeVisible();

    const firstPartyHost = new URL(UNENCRYPTED_BASE_URL).host;
    const thirdParty = [...requestHosts].filter((host) => host !== firstPartyHost);
    expect(thirdParty, `unexpected non-loopback requests: ${thirdParty.join(", ")}`).toEqual([]);
  });
});

test.describe("Setup — unencrypted-persistence banner hidden (persistent store, Transit cipher configured)", () => {
  test("the banner does not render once a mapping cipher is active", async ({ encryptedPage }) => {
    await encryptedPage.goto("/ui/setup");
    await expect(encryptedPage.getByTestId("setup-workspace-name")).toBeVisible();
    await expect(
      encryptedPage.getByTestId("setup-unencrypted-store-banner")
    ).toHaveCount(0);
    // Still not the "no persistence" condition either -- the store here is
    // persistent, only the cipher differs from the sibling instance above.
    await expect(encryptedPage.getByTestId("setup-ephemeral-store-banner")).toHaveCount(0);
  });

  test("the fixture's Transit token never appears in the page or any request", async ({
    encryptedPage,
  }) => {
    const requests: { url: string; headers: Record<string, string>; postData: string | null }[] =
      [];
    encryptedPage.on("request", (req) => {
      requests.push({ url: req.url(), headers: req.headers(), postData: req.postData() });
    });

    await encryptedPage.goto("/ui/setup");
    await expect(encryptedPage.getByTestId("setup-workspace-name")).toBeVisible();

    const bodyText = await encryptedPage.locator("body").innerText();
    expect(bodyText).not.toContain(FIXTURE_TOKEN);

    for (const req of requests) {
      const haystack = req.url + JSON.stringify(req.headers) + (req.postData ?? "");
      expect(haystack, `token leaked in request to ${req.url}`).not.toContain(FIXTURE_TOKEN);
    }

    const firstPartyHost = new URL(ENCRYPTED_BASE_URL).host;
    const thirdParty = requests.filter((r) => new URL(r.url).host !== firstPartyHost);
    expect(
      thirdParty.map((r) => r.url),
      `unexpected non-loopback requests: ${thirdParty.map((r) => r.url).join(", ")}`
    ).toEqual([]);
  });
});
