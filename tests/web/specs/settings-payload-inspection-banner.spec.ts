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
