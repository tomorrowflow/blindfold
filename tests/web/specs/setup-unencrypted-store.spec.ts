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

  test("the Enhanced local detection checkbox defaults to checked (ADR-0049, issue #366)", async ({
    unencryptedPage,
  }) => {
    // The GLiNER cascade is now the default detection path (93% vs. 21-36%
    // recall for a bare LLM alone) -- Setup's opt-in must default on wherever
    // it is offered at all (a persistent store configured), not merely be
    // present. Read-only: no submit, so this never mutates the fixture's
    // shared persistent store for the other tests in this file.
    await unencryptedPage.goto("/ui/setup");
    const checkbox = unencryptedPage.getByTestId("setup-gliner-checkbox");
    await expect(checkbox).toBeVisible();
    await expect(checkbox).toBeChecked();
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

  // Runs last in this describe block -- unlike the three read-only tests above,
  // this one submits Setup and mutates the shared 8957 store (creates a
  // workspace, flips the persisted activation flag), so it must not run before
  // them.
  test("submitting with the default-checked box provisions GLiNER end to end and never leaves the first-party origin (ADR-0049, issue #366)", async ({
    unencryptedPage,
  }) => {
    // Complements setup-shell.spec.ts's "zero gliner-provision requests on a
    // memory:// store" spec with the positive case this branch's diff is
    // actually about: on a persistent store the checkbox's new default-ON
    // state must drive a real POST .../gliner-provision through to the restart
    // prompt when Setup is submitted, not merely render checked (the read-only
    // spec above never clicks Create). This exact flow -- toggle visible ->
    // submit -> provision -> restart message -- was left untested by the
    // toggle's own original commit (a78fff3, issue #146) for lack of a
    // persistent-store fixture; #227 later added one (this file's 8957/8958)
    // but nothing came back to drive this flow through it until now.
    // serve_fixture.py wires a network-free stub GLiNER hub client for this
    // fixture too, so a real click here never reaches Hugging Face -- and
    // provisionGliner (setupApi.ts) only ever calls the same-origin management
    // API, so the browser itself should emit no non-loopback request even for
    // a "download ~197 MB" action.
    const requestHosts = new Set<string>();
    unencryptedPage.on("request", (req) => requestHosts.add(new URL(req.url()).host));

    await unencryptedPage.goto("/ui/setup");
    await expect(unencryptedPage.getByTestId("setup-gliner-checkbox")).toBeChecked();
    await unencryptedPage.getByTestId("setup-workspace-name").fill("Enhanced Detection Co");

    const [provisionRequest] = await Promise.all([
      unencryptedPage.waitForRequest(
        (req) => req.url().includes("gliner-provision") && req.method() === "POST"
      ),
      unencryptedPage.getByTestId("setup-create-btn").click(),
    ]);
    expect(provisionRequest.url()).toContain(
      "/v1/management/workspaces/enhanced-detection-co/gliner-provision"
    );

    await expect(unencryptedPage.getByTestId("setup-gliner-restart-message")).toContainText(
      "Restart Blindfold to activate enhanced detection."
    );

    const firstPartyHost = new URL(UNENCRYPTED_BASE_URL).host;
    const thirdParty = [...requestHosts].filter((host) => host !== firstPartyHost);
    expect(thirdParty, `unexpected non-loopback requests: ${thirdParty.join(", ")}`).toEqual([]);

    await unencryptedPage.getByTestId("setup-gliner-continue-btn").click();
    await expect(unencryptedPage.getByTestId("setup-ready-message")).toBeVisible();
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

  // Runs last in this describe block -- the two tests above never submit, so
  // this is the first to mutate the shared 8958 store.
  test("explicitly unticking the checkbox submits with zero gliner-provision requests (ADR-0049, issue #366)", async ({
    encryptedPage,
  }) => {
    // The default flipped to on (ADR-0049), but on a persistent store where the
    // checkbox actually renders it stays a real opt-OUT, distinct from the
    // memory:// case (setup-shell.spec.ts) where it's hidden and never reaches
    // the operator at all. Per the issue's own AC ("a deliberate skip is
    // labelled, never silent"), explicitly unticking must land the operator in
    // their workspace with zero provisioning call -- exactly the same path a
    // failed download falls through to (Setup.tsx has no separate branch for
    // "explicitly skipped" vs. "never offered"; both simply skip the `if
    // (hasPersistentStore && enhancedDetection)` block). The resulting
    // "not_provisioned" status is what Settings -> Detection's persistent
    // retry surface (settings-detection.spec.ts) and the Home/Status L3
    // dependency card (home-status-degraded.spec.ts) already render whenever
    // the model was never provisioned -- there is no separate "silent" state
    // for this path to fall into.
    const provisionRequests: string[] = [];
    encryptedPage.on("request", (req) => {
      if (req.url().includes("gliner-provision")) provisionRequests.push(req.url());
    });

    await encryptedPage.goto("/ui/setup");
    const checkbox = encryptedPage.getByTestId("setup-gliner-checkbox");
    await expect(checkbox).toBeChecked();
    await checkbox.uncheck();
    await encryptedPage.getByTestId("setup-workspace-name").fill("Skip Detection Co");
    await encryptedPage.getByTestId("setup-create-btn").click();

    await expect(encryptedPage.getByTestId("setup-ready-message")).toBeVisible();
    expect(provisionRequests).toEqual([]);
  });
});
