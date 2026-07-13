import { test as base, expect, request as pwRequest } from "@playwright/test";

// Home/Status Degraded render (issue #96): the prototype only designed the
// Protected/empty state; this file exercises the AC this slice actually adds.
// Runs against the SECOND serve_fixture.py instance (port 8952,
// BLINDFOLD_FIXTURE_STATE=degraded, see playwright.config.ts) which leaves the
// honest unconfigured-L3 default in place — a real fail-closed condition, not a
// synthetic one.

const DEGRADED_BASE_URL = "http://127.0.0.1:8952";

const test = base.extend<{ alicePage: import("@playwright/test").Page }>({
  alicePage: async ({ browser }, use) => {
    const context = await browser.newContext({
      baseURL: DEGRADED_BASE_URL,
      extraHTTPHeaders: { "x-blindfold-identity": "alice" },
    });
    const page = await context.newPage();
    await use(page);
    await context.close();
  },
});

test.describe("Degraded state", () => {
  test("banner names the failing dependency and states the fail-closed consequence", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/status");
    const banner = alicePage.getByTestId("status-banner");
    await expect(banner).toContainText("Degraded");
    await expect(banner).toContainText("L3 adjudicator");
    await expect(banner).toContainText("Requests will fail closed until this is fixed.");
  });

  test("Degraded banner shows the red-family badge, heading, and a pill naming the failing dependency", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/status");
    const banner = alicePage.getByTestId("status-banner");
    await expect(banner).toHaveClass(/bf-status-banner--degraded/);

    const badge = banner.getByTestId("status-banner-icon");
    await expect(badge).toBeVisible();
    await expect(badge).toHaveCSS("width", "46px");
    await expect(badge).toHaveCSS("border-radius", "50%");
    await expect(badge).toHaveCSS("background-color", "rgb(179, 38, 30)"); // --bf-red

    await expect(banner.getByTestId("status-banner-heading")).toHaveText("Degraded");
    await expect(banner.getByTestId("status-banner-pill")).toContainText("L3 adjudicator");
  });

  test("the unhealthy L3 dependency card shows its scrubbed detail and a red status dot", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/status");
    const card = alicePage.getByTestId("dependency-card-l3");
    await expect(card).toContainText("Unhealthy");
    await expect(card).toContainText("no L3 adjudicator configured");
    await expect(card.getByTestId("dependency-card-status-dot")).toHaveClass(
      /bf-dependency-card-status-dot--unhealthy/
    );
  });

  test("the other three dependency cards stay healthy", async ({ alicePage }) => {
    await alicePage.goto("/ui/status");
    for (const dep of ["upstream", "transit", "store"]) {
      await expect(alicePage.getByTestId(`dependency-card-${dep}`)).toContainText("Healthy");
    }
  });

  test("a management_url deep link opens this same view", async ({ alicePage }) => {
    // ADR-0027: every blocked 503's management_url resolves to /ui/status.
    await alicePage.goto("/ui/status");
    await expect(alicePage.locator("h1")).toContainText("Status");
    await expect(alicePage.getByTestId("status-banner")).toBeVisible();
  });

  test("a real fail-closed block populates the recent-blocks table with scrubbed reason, remediation, and never entity plaintext", async ({
    alicePage,
  }) => {
    const api = await pwRequest.newContext({ baseURL: DEGRADED_BASE_URL });
    const blockResp = await api.post("/v1/messages", {
      data: {
        model: "m",
        messages: [{ role: "user", content: "Please brief Persimmon Okafor-Delacroix." }],
      },
    });
    expect(blockResp.status()).toBe(503);
    await api.dispose();

    await alicePage.goto("/ui/status");
    const table = alicePage.getByTestId("blocks-table");
    await expect(table).toBeVisible();
    const row = alicePage.getByTestId("blocks-row").first();
    await expect(row).toContainText("L3 candidate-span adjudication is unavailable");
    await expect(row).toContainText(
      "Restart or configure the local L3 adjudicator (Ollama)"
    );

    const bodyText = await alicePage.locator("body").innerText();
    expect(bodyText).not.toContain("Persimmon Okafor-Delacroix");
  });
});
