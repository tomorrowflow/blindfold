import { test, expect } from "./fixtures";

// SPA fidelity (issue #173): every non-Home view used to wrap its whole page --
// header, subtitle, controls, and body -- inside one top-level `.bf-card`,
// producing a single white panel floating on the canvas. `.bf-card` is the
// discrete-block primitive (shell.css), not a page wrapper -- Home is the
// correct reference: header/subtitle/controls render directly on the bare
// `.bf-main` canvas, and only discrete content blocks (dependency cards, the
// config card) are `.bf-card`s. This spec asserts every other route now
// matches that rhythm: the page's own `<h1>` never lives inside a `.bf-card`.

test.describe("canvas rhythm — no non-Home route cards its own <h1>", () => {
  test("Entity list header sits on the bare canvas", async ({ alicePage }) => {
    await alicePage.goto("/ui/entities");
    await expect(alicePage.locator("h1")).toHaveCount(1);
    await expect(alicePage.locator(".bf-card h1")).toHaveCount(0);
  });

  test("Review inbox header sits on the bare canvas", async ({ alicePage }) => {
    await alicePage.goto("/ui/inbox");
    await expect(alicePage.locator("h1")).toHaveCount(1);
    await expect(alicePage.locator(".bf-card h1")).toHaveCount(0);
  });

  test("Access header sits on the bare canvas", async ({ alicePage }) => {
    await alicePage.goto("/ui/access");
    await expect(alicePage.locator("h1")).toHaveCount(1);
    await expect(alicePage.locator(".bf-card h1")).toHaveCount(0);
  });

  test("Settings header sits on the bare canvas", async ({ alicePage }) => {
    await alicePage.goto("/ui/settings");
    await expect(alicePage.locator("h1")).toHaveCount(1);
    await expect(alicePage.locator(".bf-card h1")).toHaveCount(0);
  });

  test("Audit log header sits on the bare canvas", async ({ alicePage }) => {
    await alicePage.goto("/ui/audit");
    await expect(alicePage.locator("h1")).toHaveCount(1);
    await expect(alicePage.locator(".bf-card h1")).toHaveCount(0);
  });

  test("Processing trace header sits on the bare canvas", async ({ alicePage }) => {
    await alicePage.goto("/ui/processing-trace");
    await expect(alicePage.locator("h1")).toHaveCount(1);
    await expect(alicePage.locator(".bf-card h1")).toHaveCount(0);
  });

  test("Graph editor header sits on the bare canvas", async ({ alicePage }) => {
    await alicePage.goto("/ui/graph");
    await expect(alicePage.locator("h1")).toHaveCount(1);
    await expect(alicePage.locator(".bf-card h1")).toHaveCount(0);
  });
});

test.describe("canvas rhythm — discrete content blocks stay individually carded", () => {
  test("Entity list still cards its table", async ({ alicePage }) => {
    await alicePage.goto("/ui/entities");
    await expect(alicePage.locator(".bf-card").locator("table")).toHaveCount(1);
  });

  test("Review inbox still cards each candidate", async ({ alicePage }) => {
    await alicePage.goto("/ui/inbox");
    await expect(alicePage.getByTestId("review-inbox-item").first()).toHaveCSS(
      "border-radius",
      "13px"
    );
  });

  test("Access still cards its roles table", async ({ alicePage }) => {
    await alicePage.goto("/ui/access");
    await expect(alicePage.locator(".bf-card").locator("table")).toHaveCount(1);
  });

  test("Settings still cards each section's content", async ({ alicePage }) => {
    await alicePage.goto("/ui/settings");
    await expect(alicePage.getByTestId("density-toggle").locator("xpath=ancestor::div[contains(@class,'bf-card')]")).toHaveCount(1);
  });

  test("Audit log still cards its table", async ({ alicePage }) => {
    await alicePage.goto("/ui/audit");
    await expect(alicePage.locator(".bf-card").locator("table")).toHaveCount(1);
  });

  test("Processing trace still cards its table", async ({ alicePage }) => {
    await alicePage.goto("/ui/processing-trace");
    await expect(alicePage.locator(".bf-card").locator("table")).toHaveCount(1);
  });
});
