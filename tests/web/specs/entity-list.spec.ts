import {
  test,
  expect,
  WORKSPACE,
  REAL_PERSON,
  PERSON_SURROGATE,
  auditEventsFor,
} from "./fixtures";

// /ui/entity-list (issue #32): rows render surrogate-space; a per-row reveal button
// re-identifies on demand, gated by the `re-identifier` role (ADR-0015).

test.describe("entity-list reveal", () => {
  test("authorized viewer: reveal shows the real value and is audited", async ({
    alicePage,
    baseURL,
  }) => {
    const page = alicePage;
    page.on("dialog", (dialog) => dialog.accept());

    await page.goto(`/ui/entity-list?workspace=${WORKSPACE}`);

    const row = page.locator("tr", { hasText: PERSON_SURROGATE });
    await row.locator("button.reveal-badge").click();

    const value = row.locator(".reveal-value");
    await expect(value).toHaveText(REAL_PERSON);

    const reveals = await auditEventsFor(baseURL!, "re-identified", "alice");
    expect(reveals.length).toBeGreaterThan(0);
  });

  test("unauthorized viewer: reveal button is locked, never fires, real value never shown", async ({
    bobPage,
  }) => {
    const page = bobPage;
    const requestUrls: string[] = [];
    page.on("request", (req) => requestUrls.push(req.url()));

    await page.goto(`/ui/entity-list?workspace=${WORKSPACE}`);

    const row = page.locator("tr", { hasText: PERSON_SURROGATE });
    const revealBtn = row.locator("button.reveal-badge");
    await expect(revealBtn).toBeVisible();
    await expect(revealBtn).toBeDisabled();
    await expect(revealBtn).toHaveText("locked");

    await expect(page.locator("body")).not.toContainText(REAL_PERSON);
    // No role -> the client never even attempts the re-identify call.
    expect(requestUrls.some((u) => u.includes("/v1/management/surrogate/"))).toBe(false);
  });
});
