import { test, expect, WORKSPACE } from "./fixtures";

// Claude Desktop 3P Gateway profile section (issue #376, ADR-0057 D7). Desktop in
// 3P Gateway mode is now an ordinary redirectable client configured by a flat-key
// JSON profile read once at launch -- this section renders that profile with the
// live host/port this page already knows, the same way the Claude Code card
// renders its own snippets, never a credential or key (entered in the Desktop UI
// itself, ADR-0057 D3).

test.describe("Connect page — Claude Desktop", () => {
  test("renders a Claude Desktop card beside Claude Code", async ({ alicePage }) => {
    await alicePage.goto("/ui/connect");
    await expect(alicePage.getByTestId("connect-card-claude-desktop")).toBeVisible();
  });

  test("the copy button yields valid JSON with the live host/port, x-api-key auth scheme, chatTabEnabled, and a non-empty inferenceModels list", async ({
    alicePage,
    baseURL,
  }) => {
    await alicePage.goto("/ui/connect");
    const card = alicePage.getByTestId("connect-card-claude-desktop");
    const snippet = card.getByTestId("copyable-snippet");
    // /v1/status's host/port arrive async (Connect.tsx's own useEffect) -- wait for
    // the real fixture port to land before parsing, matching how the Claude Code
    // snippet test (connect-shell.spec.ts) waits on the same fetch.
    await expect(snippet).toContainText(baseURL!);
    const snippetText = await snippet.locator("code").innerText();
    const profile = JSON.parse(snippetText);

    expect(profile.inferenceProvider).toBe("gateway");
    expect(profile.inferenceGatewayBaseUrl).toBe(baseURL);
    expect(profile.inferenceGatewayAuthScheme).toBe("x-api-key");
    expect(profile.chatTabEnabled).toBe(true);
    expect(Array.isArray(profile.inferenceModels)).toBe(true);
    expect(profile.inferenceModels.length).toBeGreaterThan(0);
  });

  test("never renders or asks for an API key", async ({ alicePage, baseURL }) => {
    await alicePage.goto("/ui/connect");
    const card = alicePage.getByTestId("connect-card-claude-desktop");
    await expect(card).toBeVisible();
    // No input field of any kind on this card -- the key is entered in the
    // Desktop panel itself, never here (ADR-0057 D3).
    await expect(card.locator("input")).toHaveCount(0);
    const cardText = await card.innerText();
    expect(cardText.toLowerCase()).not.toContain("sk-ant-");
    const snippet = card.getByTestId("copyable-snippet").locator("code");
    // Wait for the live host/port to land (same async render as the copy-button
    // test above) before parsing, so a still-loading snippet can't be read mid-render.
    await expect(snippet).toContainText(baseURL!);
    const snippetText = await snippet.innerText();
    expect(JSON.parse(snippetText)).not.toHaveProperty("inferenceGatewayApiKey");
  });

  test("the workspace header follows the SPA's current workspace selection: present for a non-default workspace, absent otherwise", async ({
    alicePage,
    bobPage,
  }) => {
    await alicePage.goto("/ui/connect");
    const aliceCode = alicePage
      .getByTestId("connect-card-claude-desktop")
      .getByTestId("copyable-snippet")
      .locator("code");
    // WorkspaceContext resolves the active workspace asynchronously; wait for the
    // header to actually land in the snippet before parsing, or a pre-resolution
    // read captures the transient no-header render (issue #436).
    await expect(aliceCode).toContainText("x-blindfold-workspace");
    const aliceSnippet = await aliceCode.innerText();
    expect(JSON.parse(aliceSnippet).inferenceCustomHeaders).toEqual({
      "x-blindfold-workspace": WORKSPACE,
    });

    await bobPage.goto("/ui/connect");
    const bobCode = bobPage
      .getByTestId("connect-card-claude-desktop")
      .getByTestId("copyable-snippet")
      .locator("code");
    // Bob never gets a workspace header, so we can't wait on its presence the way
    // alice's half does. Wait for WorkspaceContext's own settled signal instead
    // (the shell's workspace switcher leaving its loading state) -- otherwise a
    // still-loading render would make this negative assertion pass by accident.
    await expect(bobPage.locator(".bf-workspace-switcher--loading")).toHaveCount(0);
    const bobSnippet = await bobCode.innerText();
    expect(JSON.parse(bobSnippet)).not.toHaveProperty("inferenceCustomHeaders");
  });

  test("steps name Developer Mode, Configure Third-Party Inference, the Gateway provider, the Models field, and apply-then-relaunch", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/connect");
    const steps = alicePage.getByTestId("connect-desktop-steps");
    await expect(steps).toContainText("Developer Mode");
    await expect(steps).toContainText("Configure Third-Party Inference");
    await expect(steps).toContainText("Gateway");
    await expect(steps).toContainText("Models");
    await expect(steps).toContainText("Apply");
    await expect(steps).toContainText("quit and relaunch");
  });

  test("names the on-disk profile location under a separate Claude-3p profile directory", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/connect");
    const notes = alicePage.getByTestId("connect-claude-desktop-notes");
    await expect(notes).toContainText("Claude-3p");
    await expect(notes).toContainText("configLibrary");
    await expect(notes).toContainText("_meta.json");
  });

  test("links the CLI writer issue (#377) as the future one-command alternative", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/connect");
    const link = alicePage.getByTestId("connect-desktop-cli-writer-link");
    await expect(link).toHaveAttribute(
      "href",
      "https://github.com/tomorrowflow/blindfold/issues/377"
    );
    await expect(link).toHaveAttribute("target", "_blank");
    await expect(link).toHaveAttribute("rel", "noreferrer");
  });

  test("states the billing posture explicitly and does not reuse or imply the Claude Code subscription sentence", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/connect");
    const billingNote = alicePage.getByTestId("connect-desktop-no-subscription");
    await expect(billingNote).toContainText("subscription");
    await expect(billingNote).toContainText("metered Console API billing");
    await expect(billingNote).not.toContainText("your subscription's usage limits and billing still apply");
  });

  test("the out-of-scope copy no longer names Claude Desktop", async ({ alicePage }) => {
    await alicePage.goto("/ui/connect");
    const worksWith = alicePage.getByTestId("connect-works-with");
    await expect(worksWith).not.toContainText("Claude Desktop");
  });
});
