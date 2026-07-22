import { test, expect, WORKSPACE } from "./fixtures";

// Settings -> Detection (issue #147, ADR-0034 §5): GLiNER provisioning status +
// retry. Install-global, not per-workspace (ADR-0034 §5 -- "retry lives here, not
// on the entity list"), admin-gated the same way settings-policy.spec.ts's
// Workspace policy section is. serve_fixture.py wires a network-free stub GLiNER
// hub client + a fixture-local scratch Data directory so a real click here never
// reaches HuggingFace, and an in-memory activation-flag store double (this fixture
// has no BLINDFOLD_DATABASE_URL) so the restart-prompt state is reachable at all.
//
// Fixture roles (serve_fixture.py): alice holds admin (+ viewer/curator/re-identifier)
// on WORKSPACE ("acme"); dave holds ONLY curator (no admin).

test.describe("settings detection — section renders", () => {
  test("Detection section renders between Workspace policy and Import, not provisioned by default", async ({
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

    await expect(alicePage.getByTestId("detection-gliner-card")).toBeVisible();
    await expect(alicePage.getByTestId("detection-gliner-status-badge")).toHaveText(
      "Not provisioned"
    );
    await expect(alicePage.getByTestId("detection-gliner-retry-button")).toBeVisible();
  });
});

test.describe("settings detection — admin-gated", () => {
  test("a non-admin identity never sees the Detection section", async ({ davePage }) => {
    await davePage.goto("/ui/settings");
    await expect(davePage.getByRole("heading", { name: "Preferences" })).toBeVisible();
    await expect(davePage.getByRole("heading", { name: "Import" })).toBeVisible();
    await expect(davePage.getByRole("heading", { name: "Detection" })).not.toBeVisible();
    await expect(davePage.getByTestId("detection-gliner-card")).not.toBeVisible();
  });
});

test.describe("settings detection — retry drives provisioning", () => {
  test("clicking Retry provisions the model and shows the restart prompt", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/settings");
    await expect(alicePage.getByTestId("detection-gliner-status-badge")).toHaveText(
      "Not provisioned"
    );

    const retryButton = alicePage.getByTestId("detection-gliner-retry-button");
    const [retryRequest] = await Promise.all([
      alicePage.waitForRequest(
        (req) => req.url().includes("/detection/gliner/retry") && req.method() === "POST"
      ),
      retryButton.click(),
    ]);
    expect(retryRequest.url()).toContain(
      `/v1/management/detection/gliner/retry?workspace=${WORKSPACE}`
    );

    await expect(alicePage.getByTestId("detection-gliner-status-badge")).toHaveText("Active");
    await expect(alicePage.getByTestId("detection-gliner-restart-prompt")).toContainText(
      "Restart Blindfold to activate enhanced detection."
    );
    // Nothing left to retry once active -- the retry action is only offered for
    // the not_provisioned/verification_failed cases (ADR-0034 §5).
    await expect(retryButton).not.toBeVisible();
  });
});
