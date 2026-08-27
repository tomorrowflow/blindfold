import type { Page } from "@playwright/test";
import { test, expect, auditEventCount } from "./fixtures";

// Test connection (issue #265): the Connect page's one-click proof that a client
// pointed at `base` would actually reach Blindfold and get blindfolded traffic --
// one real, capped-cost exchange through the proxy's own loopback socket,
// reported as a typed verdict (never a bare "failed" string).
//
// serve_fixture.py stubs the shared `get_upstream_client` seam this feature's
// inner loopback `/v1/messages` call ultimately egresses through (mirroring
// tests/test_test_connection_endpoint.py's own `_echoing_stub_upstream`) --
// never this page's own `/v1/management/test-connection` endpoint, and never
// `/v1/messages` itself. A real click here drives the exact same
// mint -> leak-gate -> upstream -> restore -> resolution-gate pipeline a live
// exchange would, hermetically: no live network call, no provider credential
// needed, deterministic either way.
//
// SPA-side privacy properties this harness checks (see task brief):
//   - authorized-only re-identification: N/A -- this feature has no
//     decrypt/reveal affordance anywhere. The canary pair is minted through the
//     same reserved-namespace mechanism as other synthetic values
//     (test_connection.py's own module docstring, Q2) and never touches the
//     persistent store, entity graph, or review inbox -- there is no *user*
//     entity re-identification in this flow to gate in the first place.
//   - audit-on-decrypt: N/A for the same reason -- nothing is decrypted here.
//     Verified directly below: a passing run appends zero new audit records
//     (only blocks/upstream-errors/reveals ever do, per app.py's own audit
//     funnels -- an ordinary passing exchange writes none).
//   - browser egress hygiene: APPLICABLE, and the primary property this file
//     exists to assert. The user-supplied credential (potentially a real
//     provider secret) must reach only this page's own first-party
//     `/v1/management/test-connection` endpoint, in the POST body, never a
//     third-party origin, never persisted, never logged to the console.

const TRIGGER_401 = "trigger-test-connection-401";

/** Connect.tsx fetches GET /v1/status asynchronously and only then swaps its
 * `baseUrl` state from the compiled-in fallback (127.0.0.1:25463) to this
 * fixture's real bind port -- TestConnection.tsx receives whichever value is
 * current at click time, no re-fetch of its own. A click issued before that
 * fetch resolves silently tests against the wrong port (a real, but
 * misleading, `proxy_unreachable`). Wait for the real port to appear in an
 * already-rendered snippet first, the same signal connect-shell.spec.ts's own
 * "interpolates the real fixture host/port" test waits on. */
async function waitForRealBaseUrl(page: Page, baseURL: string) {
  const port = new URL(baseURL).port;
  await expect(page.getByTestId("connect-card-claude-code")).toContainText(`:${port}`);
}

test.describe("Test connection", () => {
  test("a successful run renders the blindfolded_ok verdict with message and remedy, no ref, and writes no new audit record", async ({
    alicePage,
    baseURL,
  }) => {
    await alicePage.goto("/ui/connect");
    await waitForRealBaseUrl(alicePage, baseURL!);
    await alicePage.getByTestId("test-connection-model").fill("claude-test-model");

    const before = await auditEventCount(baseURL!);
    await alicePage.getByTestId("test-connection-btn").click();

    const result = alicePage.getByTestId("test-connection-result");
    await expect(result).toBeVisible();
    await expect(result).toHaveAttribute("data-code", "blindfolded_ok");
    await expect(result).toHaveClass(/bf-test-connection-result--ok/);
    await expect(result).toContainText("blindfolded this exchange");
    await expect(result).toContainText("Scope: proxy-side only");
    await expect(alicePage.getByTestId("test-connection-ref")).toHaveCount(0);
    await expect(alicePage.getByTestId("test-connection-locked")).toHaveCount(0);

    // Audit-on-decrypt is N/A here (see file header) -- confirmed directly: a
    // passing exchange is not a reveal, so it must add nothing to the trail.
    const after = await auditEventCount(baseURL!);
    expect(after).toBe(before);
  });

  test("a distinct taxonomy code (upstream_auth_rejected) renders its own warning styling, never the ok styling (Q4)", async ({
    alicePage,
    baseURL,
  }) => {
    await alicePage.goto("/ui/connect");
    await waitForRealBaseUrl(alicePage, baseURL!);
    await alicePage.getByTestId("test-connection-model").fill(TRIGGER_401);
    await alicePage.getByTestId("test-connection-btn").click();

    const result = alicePage.getByTestId("test-connection-result");
    await expect(result).toBeVisible();
    await expect(result).toHaveAttribute("data-code", "upstream_auth_rejected");
    await expect(result).toHaveClass(/bf-test-connection-result--warning/);
    await expect(result).not.toHaveClass(/bf-test-connection-result--ok/);
    await expect(result).toContainText("rejected the credential");
    await expect(result).toContainText("Check the credential");
  });

  test("running it again replaces the previous verdict rather than stacking panels", async ({
    alicePage,
    baseURL,
  }) => {
    await alicePage.goto("/ui/connect");
    await waitForRealBaseUrl(alicePage, baseURL!);
    const model = alicePage.getByTestId("test-connection-model");
    const btn = alicePage.getByTestId("test-connection-btn");
    const result = alicePage.getByTestId("test-connection-result");

    await model.fill(TRIGGER_401);
    await btn.click();
    await expect(result).toHaveAttribute("data-code", "upstream_auth_rejected");

    await model.fill("claude-test-model");
    await btn.click();
    await expect(result).toHaveCount(1);
    await expect(result).toHaveAttribute("data-code", "blindfolded_ok");
  });

  test("an identity without the viewer role sees the locked state, never a verdict panel", async ({
    bobPage,
  }) => {
    await bobPage.goto("/ui/connect");
    await bobPage.getByTestId("test-connection-model").fill("claude-test-model");
    await bobPage.getByTestId("test-connection-btn").click();

    await expect(bobPage.getByTestId("test-connection-locked")).toBeVisible();
    await expect(bobPage.getByTestId("test-connection-locked")).toContainText("viewer role");
    await expect(bobPage.getByTestId("test-connection-result")).toHaveCount(0);
  });
});

test.describe("Test connection — SPA-side privacy properties", () => {
  // authorized-only re-identification and audit-on-decrypt: N/A, see file header.

  test("egress hygiene: the credential reaches only the first-party test-connection endpoint, never a third-party origin, and is never persisted or logged", async ({
    alicePage,
    baseURL,
  }) => {
    const requests: { url: string; postData: string | null }[] = [];
    alicePage.on("request", (req) => requests.push({ url: req.url(), postData: req.postData() }));
    const consoleMessages: string[] = [];
    alicePage.on("console", (msg) => consoleMessages.push(msg.text()));

    await alicePage.goto("/ui/connect");
    await waitForRealBaseUrl(alicePage, baseURL!);
    const credential = "sk-leak-check-marker-4f9c2a";
    await alicePage.getByTestId("test-connection-model").fill("claude-test-model");
    await alicePage.getByTestId("test-connection-credential").fill(credential);

    const storageBeforeClick = await alicePage.evaluate(() => ({
      local: JSON.stringify(localStorage),
      session: JSON.stringify(sessionStorage),
    }));
    expect(storageBeforeClick.local).not.toContain(credential);
    expect(storageBeforeClick.session).not.toContain(credential);

    await alicePage.getByTestId("test-connection-btn").click();
    await expect(alicePage.getByTestId("test-connection-result")).toBeVisible();

    const firstPartyHost = new URL(baseURL!).host;
    const carryingCredential = requests.filter((r) => (r.postData ?? "").includes(credential));
    expect(
      carryingCredential.length,
      "expected the credential to appear in exactly the first-party test-connection POST body"
    ).toBeGreaterThan(0);
    for (const req of carryingCredential) {
      const parsed = new URL(req.url);
      expect(parsed.host).toBe(firstPartyHost);
      expect(parsed.pathname).toBe("/v1/management/test-connection");
    }

    const thirdParty = requests.filter((r) => new URL(r.url).host !== firstPartyHost);
    expect(
      thirdParty.map((r) => r.url),
      "expected zero non-loopback requests"
    ).toEqual([]);

    const storageAfterClick = await alicePage.evaluate(() => ({
      local: JSON.stringify(localStorage),
      session: JSON.stringify(sessionStorage),
    }));
    expect(storageAfterClick.local).not.toContain(credential);
    expect(storageAfterClick.session).not.toContain(credential);

    for (const text of consoleMessages) {
      expect(text).not.toContain(credential);
    }

    // Re-identification traffic (the one endpoint this page must never call --
    // it has no decrypt/reveal affordance) stays absent throughout.
    const revealCalls = requests.filter((r) =>
      /\/v1\/management\/surrogate\/[^/]+\/real/.test(new URL(r.url).pathname)
    );
    expect(revealCalls).toEqual([]);
  });
});
