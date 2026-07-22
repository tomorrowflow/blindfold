import { test, expect } from "./fixtures";

// Settings -> Unprotected mode capability toggle (issue #188, ADR-0038): the SPA
// control for whether Unprotected mode can be invoked at all. Backed by #180's
// proxy-side capability flag (POST /v1/unprotected-mode/capability; read back via
// GET /v1/status's unprotected_mode.capability_enabled). Deliberately NOT
// admin-gated or workspace-scoped -- ADR-0038 scopes the capability to "this
// machine's proxy", the same unauthenticated loopback-only surface as
// /v1/status itself (ADR-0011/0019); unlike Workspace policy, there is no
// per-workspace identity check to hide it behind.
//
// The capability is a process-global singleton in app.py with no per-test reset
// hook, so each test here restores it to the off default before finishing --
// same convention settings-policy.spec.ts uses for the fail-closed posture.

test.describe("settings unprotected mode — section renders", () => {
  test("Unprotected mode section renders between Workspace policy and Detection, toggle OFF by default", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/settings");

    const headings = alicePage.locator("main h2");
    await expect(headings).toContainText([
      "Preferences",
      "Workspace policy",
      "Unprotected mode",
      "Detection",
      "Import",
    ]);

    const toggle = alicePage.getByTestId("unprotected-mode-capability-toggle");
    await expect(toggle).toBeVisible();
    await expect(toggle).toHaveAttribute("aria-checked", "false");
    await expect(alicePage.locator("body")).toContainText("Allow Unprotected mode");
    await expect(alicePage.locator("body")).toContainText("real values");
  });
});

test.describe("settings unprotected mode — flip the capability on", () => {
  test("flipping the toggle ON calls the capability endpoint and shows the danger note + danger card treatment", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/settings");
    const toggle = alicePage.getByTestId("unprotected-mode-capability-toggle");
    await expect(toggle).toHaveAttribute("aria-checked", "false");

    const [postRequest] = await Promise.all([
      alicePage.waitForRequest(
        (req) =>
          req.url().includes("/v1/unprotected-mode/capability") && req.method() === "POST"
      ),
      toggle.click(),
    ]);
    expect(postRequest.postDataJSON()).toEqual({ enabled: true });

    await expect(toggle).toHaveAttribute("aria-checked", "true");
    await expect(alicePage.getByTestId("unprotected-mode-danger-note")).toContainText(
      "Unprotected mode can now be invoked"
    );
    await expect(alicePage.getByTestId("unprotected-mode-icon")).toHaveClass(
      /bf-policy-icon-badge--danger/
    );

    // Flip back so later tests (and any other spec sharing this fixture instance's
    // process-global UnprotectedMode singleton) start from the off default.
    await Promise.all([
      alicePage.waitForRequest(
        (req) =>
          req.url().includes("/v1/unprotected-mode/capability") && req.method() === "POST"
      ),
      toggle.click(),
    ]);
    await expect(toggle).toHaveAttribute("aria-checked", "false");
  });
});

test.describe("settings unprotected mode — reflects the proxy-side flag", () => {
  test("reloading the page reads the current capability back from /v1/status", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/settings");
    const toggle = alicePage.getByTestId("unprotected-mode-capability-toggle");
    await expect(toggle).toHaveAttribute("aria-checked", "false");

    await Promise.all([
      alicePage.waitForRequest(
        (req) =>
          req.url().includes("/v1/unprotected-mode/capability") && req.method() === "POST"
      ),
      toggle.click(),
    ]);
    await expect(toggle).toHaveAttribute("aria-checked", "true");

    await alicePage.reload();
    await expect(alicePage.getByTestId("unprotected-mode-capability-toggle")).toHaveAttribute(
      "aria-checked",
      "true"
    );

    // Restore the off default so this fixture instance's process-global
    // UnprotectedMode singleton doesn't leak state into later specs.
    await Promise.all([
      alicePage.waitForRequest(
        (req) =>
          req.url().includes("/v1/unprotected-mode/capability") && req.method() === "POST"
      ),
      alicePage.getByTestId("unprotected-mode-capability-toggle").click(),
    ]);
    await expect(alicePage.getByTestId("unprotected-mode-capability-toggle")).toHaveAttribute(
      "aria-checked",
      "false"
    );
  });
});

test.describe("settings unprotected mode — off state refuses activation end-to-end", () => {
  test("with the capability off, the proxy's control endpoint refuses to activate Unprotected mode", async ({
    alicePage,
  }) => {
    // The SPA toggle is the only surface that flips this -- this test proves the
    // AC end-to-end through the real running proxy (issue #180's control endpoint),
    // not just that the toggle renders unchecked.
    await alicePage.goto("/ui/settings");
    await expect(
      alicePage.getByTestId("unprotected-mode-capability-toggle")
    ).toHaveAttribute("aria-checked", "false");

    const resp = await alicePage.request.post("/v1/unprotected-mode", {
      data: { bound: "infinite" },
    });
    expect(resp.status()).toBe(403);
  });
});
