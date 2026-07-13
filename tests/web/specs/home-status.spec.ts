import { test, expect, WORKSPACE } from "./fixtures";

// Home/Status view (issue #96): the shell's landing page and the deep-link target of
// every blocked request's `management_url` (#91/ADR-0027). Drives the real FastAPI
// app (serve_fixture.py, default port 8951 — forced all-healthy, per that script's
// own docstring) so the Protected render is exercised against a real `/v1/status`
// response, never a mocked one.

test.describe("Protected state", () => {
  test("header shows the workspace subtitle and a live freshness indicator", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/status");
    const subtitle = alicePage.getByTestId("status-subtitle");
    await expect(subtitle).toContainText(`Live proxy status for ${WORKSPACE}`);
    await expect(subtitle).toContainText("reported by the proxy, not re-derived here.");

    const freshness = alicePage.getByTestId("status-freshness");
    await expect(freshness).toContainText(/polled \d+s ago/);
    await expect(freshness.locator(".bf-status-freshness-dot")).toBeVisible();
  });

  test("banner announces all dependencies healthy", async ({ alicePage }) => {
    await alicePage.goto("/ui/status");
    await expect(alicePage.getByTestId("status-banner")).toContainText(
      "All dependencies healthy"
    );
  });

  test("Protected banner shows a 46px round icon badge, a heading, and a healthy pill", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/status");
    const banner = alicePage.getByTestId("status-banner");
    await expect(banner).toHaveClass(/bf-status-banner--protected/);

    const badge = banner.getByTestId("status-banner-icon");
    await expect(badge).toBeVisible();
    await expect(badge).toHaveCSS("width", "46px");
    await expect(badge).toHaveCSS("border-radius", "50%");

    await expect(banner.getByTestId("status-banner-heading")).toHaveText("Protected");
    await expect(banner.getByTestId("status-banner-pill")).toHaveText(
      "All dependencies healthy"
    );
  });

  test("all four dependency cards render healthy", async ({ alicePage }) => {
    await alicePage.goto("/ui/status");
    for (const dep of ["upstream", "l3", "transit", "store"]) {
      const card = alicePage.getByTestId(`dependency-card-${dep}`);
      await expect(card).toBeVisible();
      await expect(card).toContainText("Healthy");
    }
  });

  test("healthy dependency cards show a 34px icon badge, an ok status dot with a ring, and mono latency", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/status");
    for (const dep of ["l3", "transit", "store"]) {
      const card = alicePage.getByTestId(`dependency-card-${dep}`);
      const badge = card.getByTestId("dependency-card-icon");
      await expect(badge).toHaveCSS("width", "34px");

      const dot = card.getByTestId("dependency-card-status-dot");
      await expect(dot).toHaveClass(/bf-dependency-card-status-dot--ok/);
      await expect(dot).toHaveCSS("box-shadow", /.+/);

      const latency = card.getByTestId("dependency-card-latency");
      await expect(latency).toHaveText("8ms");
      await expect(latency).toHaveCSS("font-family", /IBM Plex Mono/);
    }
  });

  test("upstream's card has an icon badge and status dot but no fabricated latency", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/status");
    const card = alicePage.getByTestId("dependency-card-upstream");
    await expect(card.getByTestId("dependency-card-icon")).toHaveCSS("width", "34px");
    await expect(card.getByTestId("dependency-card-status-dot")).toHaveClass(
      /bf-dependency-card-status-dot--ok/
    );
    await expect(card.getByTestId("dependency-card-latency")).toHaveCount(0);
  });

  test("recent-blocks empty state shows the icon circle and the two-line copy", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/status");
    const empty = alicePage.getByTestId("blocks-empty");
    await expect(empty).toBeVisible();
    await expect(empty.getByTestId("blocks-empty-icon")).toHaveCSS("width", "44px");
    await expect(empty.getByTestId("blocks-empty-icon")).toHaveCSS("border-radius", "50%");
    await expect(empty).toContainText("No requests blocked in the last 15 minutes");
  });

  test("config card shows non-secret values and the read-only pill", async ({ alicePage }) => {
    await alicePage.goto("/ui/status");
    const card = alicePage.getByTestId("config-card");
    await expect(card).toContainText("Read-only");
    await expect(card).toContainText("Fail-closed policy");
    await expect(card).toContainText("fail-closed");
    await expect(card).toContainText(
      "there is no in-app config editor, and secrets are never shown"
    );
  });

  test("review inbox rail card links to the review inbox", async ({ alicePage }) => {
    await alicePage.goto("/ui/status");
    const card = alicePage.getByTestId("review-inbox-card");
    await expect(card).toContainText("awaiting review");
    await expect(card).toHaveAttribute("href", "/ui/inbox");
  });

  test("review inbox card shows a curator-green icon badge, a provisional-surrogates subline, and a link row", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/status");
    const card = alicePage.getByTestId("review-inbox-card");

    const badge = card.getByTestId("review-inbox-card-icon");
    await expect(badge).toBeVisible();
    await expect(badge).toHaveCSS("background-color", "rgb(231, 243, 236)"); // --bf-curator-bg
    await expect(badge).toHaveCSS("color", "rgb(35, 122, 82)"); // --bf-curator

    await expect(card).toContainText("provisional surrogates");
    await expect(card.getByTestId("review-inbox-card-link-row")).toContainText(
      "Open review inbox"
    );
  });

  test("no cloud model name ever appears in L3 copy", async ({ alicePage }) => {
    await alicePage.goto("/ui/status");
    const body = await alicePage.locator("body").innerText();
    expect(body.toLowerCase()).not.toContain("claude");
    expect(body.toLowerCase()).not.toContain("sonnet");
  });
});
