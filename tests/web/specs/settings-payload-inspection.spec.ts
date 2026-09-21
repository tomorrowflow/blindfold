import { test, expect } from "./fixtures";

// Settings -> Payload inspection arming control (issue #402, ADR-0059 §4). Backs
// onto #398's proxy-side arm/disarm endpoints (GET/POST/DELETE
// /v1/management/payload-inspection, admin-gated). Reuses the established
// .bf-policy-toggle idiom (Workspace policy, Unprotected mode) rather than a new
// component. Fixture roles (serve_fixture.py): alice holds admin (+ viewer/
// curator/re-identifier) on WORKSPACE ("acme"); dave holds ONLY curator (no
// admin); bob holds no role anywhere.

test.describe("settings payload inspection — section renders", () => {
  test("Payload inspection section renders between Unprotected mode and Detection, toggle OFF by default", async ({
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

    // ADR-0059 §4 / issue #402 AC: states what arming retains, and that
    // entity-free is not the same as harmless.
    await expect(alicePage.locator("body")).toContainText("entity-free");
    await expect(alicePage.locator("body")).toContainText("credentials");
  });
});

test.describe("settings payload inspection — arm as admin", () => {
  test("flipping the toggle ON calls the arm endpoint and shows the danger note + danger card treatment", async ({
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

    await expect(toggle).toHaveAttribute("aria-checked", "true");
    await expect(alicePage.getByTestId("payload-inspection-danger-note")).toContainText(
      "Payload inspection is armed"
    );
    await expect(alicePage.getByTestId("payload-inspection-icon")).toHaveClass(
      /bf-policy-icon-badge--danger/
    );

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
