import { test, expect, auditEventsFor } from "./fixtures";

// Settings -> Payload inspection arming control (issue #402, ADR-0059 §4). Backs
// onto #398's proxy-side arm/disarm endpoints (GET/POST/DELETE
// /v1/management/payload-inspection, admin-gated). Reuses the established
// .bf-policy-toggle idiom (Workspace policy, Unprotected mode) rather than a new
// component. Fixture roles (serve_fixture.py): alice holds admin (+ viewer/
// curator/re-identifier) on WORKSPACE ("acme"); dave holds ONLY curator (no
// admin); bob holds no role anywhere.

test.describe("settings payload inspection — section renders", () => {
  test("Payload inspection section renders between Unprotected mode and Detection, toggle OFF and window defaulted to 30 minutes", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/settings");

    const headings = alicePage.locator("main h2");
    await expect(headings).toContainText([
      "Preferences",
      "Workspace policy",
      "Unprotected mode",
      "Payload inspection",
      "Detection",
      "Import",
    ]);

    const toggle = alicePage.getByTestId("payload-inspection-arm-toggle");
    await expect(toggle).toBeVisible();
    await expect(toggle).toHaveAttribute("aria-checked", "false");

    // Issue #433 AC: "Settings offers the three windows, defaulting to 30 minutes."
    const select = alicePage.getByTestId("payload-inspection-window-select");
    await expect(select).toHaveValue("30m");
    await expect(select.locator("option")).toHaveText([
      "30 minutes (25 exchanges)",
      "2 hours (100 exchanges)",
      "Until disarmed (200 exchanges)",
    ]);

    // ADR-0059 §4 / issue #402 AC: states what arming retains, and that
    // entity-free is not the same as harmless.
    await expect(alicePage.locator("body")).toContainText("entity-free");
    await expect(alicePage.locator("body")).toContainText("credentials");
  });
});

test.describe("settings payload inspection — arm as admin", () => {
  test("flipping the toggle ON calls the arm endpoint with the default window and shows the danger note + danger card treatment", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/settings");
    const toggle = alicePage.getByTestId("payload-inspection-arm-toggle");
    await expect(toggle).toHaveAttribute("aria-checked", "false");

    const [postRequest] = await Promise.all([
      alicePage.waitForRequest(
        (req) =>
          req.url().includes("/v1/management/payload-inspection") && req.method() === "POST"
      ),
      toggle.click(),
    ]);
    expect(postRequest.url()).toContain("workspace=acme");
    expect(postRequest.url()).toContain("window=30m");

    await expect(toggle).toHaveAttribute("aria-checked", "true");
    await expect(alicePage.getByTestId("payload-inspection-danger-note")).toContainText(
      "Payload inspection is armed"
    );
    await expect(alicePage.getByTestId("payload-inspection-icon")).toHaveClass(
      /bf-policy-icon-badge--danger/
    );
    // Issue #433 AC: "changing it means disarming and re-arming" -- the select
    // is disabled once armed.
    await expect(alicePage.getByTestId("payload-inspection-window-select")).toBeDisabled();

    // Flip back so later tests (and any other spec sharing this fixture instance's
    // process-global PayloadInspection singleton) start from the off default.
    await Promise.all([
      alicePage.waitForRequest(
        (req) =>
          req.url().includes("/v1/management/payload-inspection") && req.method() === "DELETE"
      ),
      toggle.click(),
    ]);
    await expect(toggle).toHaveAttribute("aria-checked", "false");
  });

  test("picking 2 hours before arming carries that window through to the arm request and the danger note", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/settings");
    const toggle = alicePage.getByTestId("payload-inspection-arm-toggle");
    const select = alicePage.getByTestId("payload-inspection-window-select");
    await select.selectOption("2h");

    const [postRequest] = await Promise.all([
      alicePage.waitForRequest(
        (req) =>
          req.url().includes("/v1/management/payload-inspection") && req.method() === "POST"
      ),
      toggle.click(),
    ]);
    expect(postRequest.url()).toContain("window=2h");
    await expect(alicePage.getByTestId("payload-inspection-danger-note")).toContainText(
      "2 hours"
    );

    await Promise.all([
      alicePage.waitForRequest(
        (req) =>
          req.url().includes("/v1/management/payload-inspection") && req.method() === "DELETE"
      ),
      toggle.click(),
    ]);
    await expect(toggle).toHaveAttribute("aria-checked", "false");
  });

  // Coverage gap this browser-verify pass found: the "until disarmed" window's
  // danger-note/banner text is exercised elsewhere ONLY against a mocked
  // `page.route` GET response (settings-payload-inspection-banner.spec.ts's
  // "until-disarmed banner text" test, which fabricates retained_count=3 --
  // there is no way to actually populate a retained leaf by clicking through
  // this page alone, since that requires a real /v1/messages exchange). That
  // leaves the real backend contract for `window=until_disarmed` -- the real
  // POST, the real count_bound=200 threading through to both the Settings
  // danger note AND the shell-level persistent banner (PayloadInspectionBanner
  // renders above every routed view, including this one -- Shell.tsx) -- with
  // zero real, unmocked coverage. This test drives the real toggle click with
  // "Until disarmed" selected and reads both surfaces' real (0 of 200, since
  // no exchange has happened) response back from `blindfold.app` itself.
  test("picking 'Until disarmed' before arming carries that window through to a real arm request, the danger note, the persistent banner (real, unmocked 0-of-200 count), and the audit trail", async ({
    alicePage,
    baseURL,
  }) => {
    await alicePage.goto("/ui/settings");
    const toggle = alicePage.getByTestId("payload-inspection-arm-toggle");
    const select = alicePage.getByTestId("payload-inspection-window-select");
    await select.selectOption("until_disarmed");

    const [postRequest] = await Promise.all([
      alicePage.waitForRequest(
        (req) =>
          req.url().includes("/v1/management/payload-inspection") && req.method() === "POST"
      ),
      toggle.click(),
    ]);
    expect(postRequest.url()).toContain("window=until_disarmed");

    // Settings danger note: no timer wording, states the real 200-exchange bound.
    const dangerNote = alicePage.getByTestId("payload-inspection-danger-note");
    await expect(dangerNote).toContainText("Until disarmed");
    await expect(dangerNote).toContainText("up to 200 exchanges");
    await expect(dangerNote).toContainText("until it is disarmed");
    await expect(dangerNote).not.toContainText("auto-disarms");

    // Persistent banner (Shell-level, rendered above this same route): a real,
    // unmocked GET against blindfold.app reports retained_count=0 (nothing was
    // ever retained -- no /v1/messages exchange occurred), never a countdown.
    const banner = alicePage.getByTestId("payload-inspection-banner");
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("until disarmed");
    await expect(alicePage.getByTestId("payload-inspection-banner-retained-count")).toContainText(
      "0 of 200"
    );
    await expect(alicePage.getByTestId("payload-inspection-banner-remaining")).not.toBeAttached();

    // Audit trail: the arm event's reason names the real chosen window (app.py's
    // `arm_payload_inspection`), not just the default -- a real record, read back
    // from the audit endpoint the way an authorized auditor would (never scraped
    // from the page's own DOM/network capture).
    const armedEvents = await auditEventsFor(baseURL!, "payload-inspection-armed", "alice");
    expect(armedEvents.some((e) => e.reason.includes("window=until_disarmed"))).toBe(true);

    await Promise.all([
      alicePage.waitForRequest(
        (req) =>
          req.url().includes("/v1/management/payload-inspection") && req.method() === "DELETE"
      ),
      toggle.click(),
    ]);
    await expect(toggle).toHaveAttribute("aria-checked", "false");
    await expect(banner).not.toBeAttached();
  });
});

test.describe("settings payload inspection — admin-gated", () => {
  test("a non-admin identity (dave, curator only) still sees the section, with the toggle disabled and a stated reason", async ({
    davePage,
  }) => {
    await davePage.goto("/ui/settings");

    await expect(davePage.getByRole("heading", { name: "Payload inspection" })).toBeVisible();

    const toggle = davePage.getByTestId("payload-inspection-arm-toggle");
    await expect(toggle).toBeVisible();
    await expect(toggle).toBeDisabled();
    await expect(toggle).toHaveAttribute("aria-checked", "false");
    await expect(davePage.getByTestId("payload-inspection-window-select")).toBeDisabled();
    await expect(davePage.getByTestId("payload-inspection-admin-note")).toContainText(
      "Requires the admin role"
    );
  });
});

// Browser-side privacy gate (issue #402 verification): this section carries no
// entity/surrogate data at all (payloadInspectionApi.ts touches only
// GET/POST/DELETE /v1/management/payload-inspection), so the only property that
// applies here is "nothing egresses to a third-party origin" -- matching
// settings-unprotected-mode.spec.ts's established per-flow pattern.
test.describe("settings payload inspection — egress hygiene", () => {
  test("arming and disarming issues zero non-loopback requests", async ({ alicePage, baseURL }) => {
    const hosts = new Set<string>();
    alicePage.on("request", (req) => hosts.add(new URL(req.url()).host));

    await alicePage.goto("/ui/settings");
    const toggle = alicePage.getByTestId("payload-inspection-arm-toggle");
    await Promise.all([
      alicePage.waitForRequest(
        (req) =>
          req.url().includes("/v1/management/payload-inspection") && req.method() === "POST"
      ),
      toggle.click(),
    ]);
    await expect(toggle).toHaveAttribute("aria-checked", "true");

    await Promise.all([
      alicePage.waitForRequest(
        (req) =>
          req.url().includes("/v1/management/payload-inspection") && req.method() === "DELETE"
      ),
      toggle.click(),
    ]);
    await expect(toggle).toHaveAttribute("aria-checked", "false");

    const firstPartyHost = new URL(baseURL!).host;
    const thirdParty = [...hosts].filter((host) => host !== firstPartyHost);
    expect(thirdParty, `unexpected non-loopback requests: ${thirdParty.join(", ")}`).toEqual([]);
  });
});
