import { test, expect } from "./fixtures";

// Persistent armed banner + auto-disarm notification (issue #402, ADR-0059 §4).
// The banner is shell-level (frontend/src/components/PayloadInspectionBanner.tsx),
// rendered above every routed view, not just the Settings route -- so it stays
// visible "wherever the operator is in the app" per the issue's own acceptance
// criteria. Fixture roles: alice holds admin on WORKSPACE ("acme").

test.describe("payload inspection — persistent banner", () => {
  test("arming from Settings shows a banner with remaining time, visible from Home too, and disarming clears it", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/settings");
    await expect(alicePage.getByTestId("payload-inspection-banner")).not.toBeAttached();

    const toggle = alicePage.getByTestId("payload-inspection-arm-toggle");
    await Promise.all([
      alicePage.waitForRequest(
        (req) =>
          req.url().includes("/v1/management/payload-inspection") && req.method() === "POST"
      ),
      toggle.click(),
    ]);

    const banner = alicePage.getByTestId("payload-inspection-banner");
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("Payload inspection is armed");
    await expect(alicePage.getByTestId("payload-inspection-banner-remaining")).toContainText(
      /\d+:\d{2}/
    );

    // Visible from another route too, not just Settings.
    await alicePage.goto("/ui/");
    await expect(alicePage.getByTestId("payload-inspection-banner")).toBeVisible();

    // Disarming (from Settings) clears the banner everywhere.
    await alicePage.goto("/ui/settings");
    await Promise.all([
      alicePage.waitForRequest(
        (req) =>
          req.url().includes("/v1/management/payload-inspection") && req.method() === "DELETE"
      ),
      alicePage.getByTestId("payload-inspection-arm-toggle").click(),
    ]);
    await expect(alicePage.getByTestId("payload-inspection-banner")).not.toBeAttached();
  });
});

test.describe("payload inspection — auto-disarm notification", () => {
  test("a poll that observes armed -> not-armed (without this tab's own disarm click) raises a toast and clears the banner", async ({
    alicePage,
  }) => {
    let armed = true;
    await alicePage.route("**/v1/management/payload-inspection*", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            armed,
            remaining_seconds: armed ? 60 : null,
          }),
        });
        return;
      }
      await route.continue();
    });

    await alicePage.goto("/ui/settings");
    await expect(alicePage.getByTestId("payload-inspection-banner")).toBeVisible();
    await expect(alicePage.getByTestId("payload-inspection-arm-toggle")).toHaveAttribute(
      "aria-checked",
      "true"
    );

    // Simulate the proxy's own 30-minute auto-disarm firing server-side, between
    // this tab's polls -- nothing here clicked the toggle.
    armed = false;

    await expect(alicePage.getByTestId("payload-inspection-banner")).not.toBeAttached({
      timeout: 10000,
    });
    await expect(alicePage.getByTestId("payload-inspection-arm-toggle")).toHaveAttribute(
      "aria-checked",
      "false"
    );
    const toast = alicePage.getByTestId("toast");
    await expect(toast).toBeVisible();
    await expect(toast).toContainText("auto-disarmed");
  });
});

// Browser-side privacy gate (issue #402): the armed flag is process-global on the
// proxy (payload_inspection.py's docstring: "never the shared store", but still
// one flag shared across every identity hitting this process) and the read
// endpoint is admin-gated (ADR-0059 §4: "a non-admin identity is never shown the
// banner, matching the read gate exactly"). This is the authorized-only-viewing
// property applied to this slice's one piece of sensitive state (whether recent
// payload text is being retained at all) rather than to an entity's real value --
// the closest thing this diff has to "a real value made visible to an
// unauthorized viewer." Assert it holds even when the flag is genuinely armed by
// another identity, not just when dave's own attempt to arm is refused.
test.describe("payload inspection — armed state is not visible to a non-admin identity", () => {
  test("alice arms it; dave (curator only, no admin) never sees the banner, never sees the toggle flip, and his session never requests the endpoint", async ({
    alicePage,
    davePage,
  }) => {
    const daveRequestsToEndpoint: string[] = [];
    davePage.on("request", (req) => {
      if (req.url().includes("/v1/management/payload-inspection")) {
        daveRequestsToEndpoint.push(`${req.method()} ${req.url()}`);
      }
    });

    await alicePage.goto("/ui/settings");
    await Promise.all([
      alicePage.waitForRequest(
        (req) =>
          req.url().includes("/v1/management/payload-inspection") && req.method() === "POST"
      ),
      alicePage.getByTestId("payload-inspection-arm-toggle").click(),
    ]);
    await expect(alicePage.getByTestId("payload-inspection-banner")).toBeVisible();

    // dave loads two routes (Settings, where the toggle lives, and Home, where the
    // banner would render if it were shown to him) after the flag is genuinely on.
    await davePage.goto("/ui/settings");
    await expect(davePage.getByTestId("payload-inspection-banner")).not.toBeAttached();
    await expect(davePage.getByTestId("payload-inspection-arm-toggle")).toHaveAttribute(
      "aria-checked",
      "false"
    );
    await davePage.goto("/ui/");
    await expect(davePage.getByTestId("payload-inspection-banner")).not.toBeAttached();

    // Give the shell-level poll loop (5s interval) a chance to have fired if it
    // were ever going to -- it shouldn't, since isAdmin gates the poll itself.
    await davePage.waitForTimeout(500);
    expect(
      daveRequestsToEndpoint,
      `dave's session should never request the admin-gated endpoint: ${daveRequestsToEndpoint.join(", ")}`
    ).toEqual([]);

    // Clean up: disarm as alice so later specs sharing this fixture instance's
    // process-global PayloadInspection singleton start from the off default.
    await Promise.all([
      alicePage.waitForRequest(
        (req) =>
          req.url().includes("/v1/management/payload-inspection") && req.method() === "DELETE"
      ),
      alicePage.getByTestId("payload-inspection-arm-toggle").click(),
    ]);
    await expect(alicePage.getByTestId("payload-inspection-banner")).not.toBeAttached();
  });
});
