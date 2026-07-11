import { test, expect } from "./fixtures";

// Home/Status view (issue #96): the shell's landing page and the deep-link target of
// every blocked request's `management_url` (#91/ADR-0027). Drives the real FastAPI
// app (serve_fixture.py, default port 8951 — forced all-healthy, per that script's
// own docstring) so the Protected render is exercised against a real `/v1/status`
// response, never a mocked one.

test.describe("Protected state", () => {
  test("banner announces all dependencies healthy", async ({ alicePage }) => {
    await alicePage.goto("/ui/status");
    await expect(alicePage.getByTestId("status-banner")).toContainText(
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

  test("no cloud model name ever appears in L3 copy", async ({ alicePage }) => {
    await alicePage.goto("/ui/status");
    const body = await alicePage.locator("body").innerText();
    expect(body.toLowerCase()).not.toContain("claude");
    expect(body.toLowerCase()).not.toContain("sonnet");
  });
});
