import { test, expect, WORKSPACE } from "./fixtures";

// Settings -> Workspace policy (issue #120, ADR-0009): the fail-closed safety toggle,
// consuming the policy API shipped by #118 (GET/PUT
// /v1/management/workspaces/{slug}/policy). Sits between Preferences and Import (#116)
// per the comp (design brief §3.7). Admin-gated: only an admin identity may read/flip
// the posture; a non-admin never sees the toggle at all (same convention as the Access
// nav item, frontend/src/components/nav.ts's `requiresRole: "admin"`).
//
// Fixture roles (serve_fixture.py): alice holds admin (+ viewer/curator/re-identifier)
// on WORKSPACE ("acme"); dave holds ONLY curator (no admin).

test.describe("settings workspace policy — section renders", () => {
  test("Workspace policy section renders between Preferences and Import, toggle ON by default", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/settings");

    const headings = alicePage.locator("main h2");
    await expect(headings).toContainText(["Preferences", "Workspace policy", "Import"]);

    await expect(alicePage.getByTestId("policy-lock-icon")).toBeVisible();
    const toggle = alicePage.getByTestId("policy-fail-closed-toggle");
    await expect(toggle).toBeVisible();
    await expect(toggle).toHaveAttribute("aria-checked", "true");
    await expect(alicePage.locator("body")).toContainText(
      "Fail closed on dependency loss"
    );
  });
});

test.describe("settings workspace policy — flip to degrade opt-in", () => {
  test("flipping the toggle OFF calls PUT and shows the danger note + danger card treatment", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/settings");
    const toggle = alicePage.getByTestId("policy-fail-closed-toggle");
    await expect(toggle).toHaveAttribute("aria-checked", "true");

    const [putRequest] = await Promise.all([
      alicePage.waitForRequest(
        (req) => req.url().includes("/policy") && req.method() === "PUT"
      ),
      toggle.click(),
    ]);
    expect(putRequest.url()).toContain(`/v1/management/workspaces/${WORKSPACE}/policy`);
    expect(putRequest.postDataJSON()).toEqual({ deterministic_only: true });

    await expect(toggle).toHaveAttribute("aria-checked", "false");
    await expect(alicePage.getByTestId("policy-danger-note")).toContainText(
      "L3 candidate-span adjudication is skipped"
    );
    await expect(alicePage.getByTestId("policy-lock-icon")).toHaveClass(
      /bf-policy-icon-badge--danger/
    );

    // Flip back so later tests (and the persisted-store round-trip test below)
    // start from the fail-closed default.
    await Promise.all([
      alicePage.waitForRequest(
        (req) => req.url().includes("/policy") && req.method() === "PUT"
      ),
      toggle.click(),
    ]);
    await expect(toggle).toHaveAttribute("aria-checked", "true");
  });
});

test.describe("settings workspace policy — admin-gated", () => {
  test("a non-admin identity never sees the Workspace policy section", async ({ davePage }) => {
    await davePage.goto("/ui/settings");
    await expect(davePage.getByRole("heading", { name: "Preferences" })).toBeVisible();
    await expect(davePage.getByRole("heading", { name: "Import" })).toBeVisible();
    await expect(
      davePage.getByRole("heading", { name: "Workspace policy" })
    ).not.toBeVisible();
    await expect(davePage.getByTestId("policy-fail-closed-toggle")).not.toBeVisible();
  });
});

test.describe("settings workspace policy — status surface reflects the posture", () => {
  test("flipping the toggle OFF is reflected in the Home/Status config card's fail-closed policy", async ({
    alicePage,
  }) => {
    // Issue #126 AC: "/v1/status's config summary reflects the [active workspace]
    // policy" -- this is the end-to-end proof that the Settings toggle and the
    // Home/Status Configuration card (ConfigCard.tsx) actually agree, not just
    // that each independently renders something.
    await alicePage.goto("/ui/settings");
    const toggle = alicePage.getByTestId("policy-fail-closed-toggle");
    await expect(toggle).toHaveAttribute("aria-checked", "true");

    await Promise.all([
      alicePage.waitForRequest(
        (req) => req.url().includes("/policy") && req.method() === "PUT"
      ),
      toggle.click(),
    ]);
    await expect(toggle).toHaveAttribute("aria-checked", "false");

    await alicePage.goto("/ui/status");
    await expect(alicePage.getByTestId("config-card")).toContainText("deterministic-only");

    // Restore the fail-closed default so this store doesn't leak state into
    // later specs that share this fixture instance.
    await alicePage.goto("/ui/settings");
    await Promise.all([
      alicePage.waitForRequest(
        (req) => req.url().includes("/policy") && req.method() === "PUT"
      ),
      alicePage.getByTestId("policy-fail-closed-toggle").click(),
    ]);
    await expect(alicePage.getByTestId("policy-fail-closed-toggle")).toHaveAttribute(
      "aria-checked",
      "true"
    );
  });
});

test.describe("settings workspace policy — posture round-trips", () => {
  test("flipping the toggle then reloading the page persists the degrade opt-in", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/settings");
    const toggle = alicePage.getByTestId("policy-fail-closed-toggle");
    await expect(toggle).toHaveAttribute("aria-checked", "true");

    await Promise.all([
      alicePage.waitForRequest(
        (req) => req.url().includes("/policy") && req.method() === "PUT"
      ),
      toggle.click(),
    ]);
    await expect(toggle).toHaveAttribute("aria-checked", "false");

    await alicePage.reload();
    await expect(alicePage.getByTestId("policy-fail-closed-toggle")).toHaveAttribute(
      "aria-checked",
      "false"
    );
    await expect(alicePage.getByTestId("policy-danger-note")).toBeVisible();

    // Restore the fail-closed default so this store doesn't leak state into
    // later specs that share this fixture instance.
    await Promise.all([
      alicePage.waitForRequest(
        (req) => req.url().includes("/policy") && req.method() === "PUT"
      ),
      alicePage.getByTestId("policy-fail-closed-toggle").click(),
    ]);
    await expect(alicePage.getByTestId("policy-fail-closed-toggle")).toHaveAttribute(
      "aria-checked",
      "true"
    );
  });
});
