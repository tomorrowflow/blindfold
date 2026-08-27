import { test, expect, WORKSPACE } from "./fixtures";

// Connect (issue #264): a read-only reference page teaching how to point an LLM
// client at this Blindfold instance. It renders only static prose, the caller's
// own placeholder text (<your-token>, never a real credential), and non-secret
// /v1/status config fields (host/port) plus the active workspace's slug.
//
// SPA-side privacy properties this harness checks (see task brief):
//   - authorized-only re-identification: N/A -- this page has no decrypt/reveal
//     affordance anywhere; it never fetches or displays a real entity value.
//   - audit-on-decrypt: N/A for the same reason -- there is no reveal action here
//     to audit, allowed or denied.
//   - browser egress hygiene: APPLICABLE -- asserted below (first-party-only
//     traffic, and the only network call this page makes is GET /v1/status).
//     shell-egress-hygiene.spec.ts's SHELL_ROUTES sweep also now covers
//     /ui/connect for the zero-non-loopback-origin property at the shell level.

test.describe("Connect page", () => {
  test("renders all three client cards", async ({ alicePage }) => {
    await alicePage.goto("/ui/connect");
    await expect(alicePage.getByTestId("connect-card-claude-code")).toBeVisible();
    await expect(alicePage.getByTestId("connect-card-openai-sdk")).toBeVisible();
    await expect(alicePage.getByTestId("connect-card-codex")).toBeVisible();
  });

  test("states the localhost trust boundary, always visible above the connection snippets, linking to BETA.md", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/connect");
    const banner = alicePage.getByTestId("connect-trust-boundary");
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("unauthenticated on localhost");
    await expect(banner).toContainText("configured provider credential");
    await expect(banner).toContainText("restored");

    const link = banner.getByTestId("connect-trust-boundary-link");
    await expect(link).toHaveAttribute("href", /BETA\.md/);
    await expect(link).toHaveAttribute("target", "_blank");
    await expect(link).toHaveAttribute("rel", "noreferrer");

    // "Adjacent to the snippets" -- precedes the first client card in DOM order,
    // not hidden behind a tab/toggle inside it.
    const card = alicePage.getByTestId("connect-card-claude-code");
    const boxes = await Promise.all([banner.boundingBox(), card.boundingBox()]);
    expect(boxes[0]!.y).toBeLessThan(boxes[1]!.y);
  });

  test("Claude Code's terminal snippet interpolates the real fixture host/port from /v1/status, not the compiled-in default", async ({
    alicePage,
    baseURL,
  }) => {
    await alicePage.goto("/ui/connect");
    const card = alicePage.getByTestId("connect-card-claude-code");
    const snippet = card.locator(".bf-connect-tab-panel").getByTestId("copyable-snippet").first();
    await expect(snippet).toContainText(`export ANTHROPIC_BASE_URL=${baseURL}`);
    // 25463 is Connect.tsx's own compiled-in fallback constant, which happens to
    // collide with config.py's DEFAULT_PORT -- asserting the real fixture port
    // (8951) is what actually distinguishes "fetched from /v1/status" from
    // "silently fell back to the hardcoded default" (see serve_fixture.py's
    // BLINDFOLD_HOST/BLINDFOLD_PORT mirroring, added for this very reason).
    await expect(snippet).not.toContainText("25463");
  });

  test("Claude Code tabs toggle between This terminal and Always, changing aria-selected and panel content", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/connect");
    const card = alicePage.getByTestId("connect-card-claude-code");
    const terminalTab = card.getByTestId("connect-claude-code-tab-terminal");
    const alwaysTab = card.getByTestId("connect-claude-code-tab-always");
    const panel = card.locator(".bf-connect-tab-panel");

    await expect(terminalTab).toHaveAttribute("aria-selected", "true");
    await expect(alwaysTab).toHaveAttribute("aria-selected", "false");
    await expect(panel).toContainText("ANTHROPIC_AUTH_TOKEN=<your-token>");
    await expect(panel).not.toContainText("settings.json");
    // "Good to know" sits outside the tab panel entirely -- always visible.
    await expect(card.getByTestId("connect-claude-code-notes")).toBeVisible();

    await alwaysTab.click();
    await expect(alwaysTab).toHaveAttribute("aria-selected", "true");
    await expect(terminalTab).toHaveAttribute("aria-selected", "false");
    await expect(panel).toContainText('"ANTHROPIC_BASE_URL"');
    await expect(panel.getByTestId("connect-consequence-warning")).toBeVisible();
  });

  test("the workspace-scoping snippet appears for a non-default active workspace (alice/acme) and is absent when there is no active workspace (bob)", async ({
    alicePage,
    bobPage,
  }) => {
    await alicePage.goto("/ui/connect");
    await expect(alicePage.getByTestId("connect-card-claude-code")).toContainText(
      `x-blindfold-workspace: ${WORKSPACE}`
    );

    await bobPage.goto("/ui/connect");
    await expect(bobPage.getByTestId("connect-card-claude-code")).not.toContainText(
      "x-blindfold-workspace"
    );
  });

  test("Good to know notes don't claim count_tokens is unimplemented (issue #267 shipped it)", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/connect");
    const notes = alicePage.getByTestId("connect-claude-code-notes");
    await expect(notes).not.toContainText("doesn't implement");
    await expect(notes).not.toContainText("count_tokens");
  });

  test("Codex CLI card shows the not-supported pill and an issue link with correct attributes (never navigated)", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/connect");
    const card = alicePage.getByTestId("connect-card-codex");
    await expect(card.getByTestId("connect-codex-not-supported")).toContainText(
      "Not supported yet"
    );
    const link = card.getByTestId("connect-codex-issue-link");
    await expect(link).toHaveAttribute(
      "href",
      "https://github.com/tomorrowflow/blindfold/issues/263"
    );
    await expect(link).toHaveAttribute("target", "_blank");
    await expect(link).toHaveAttribute("rel", "noreferrer");
  });

  test("copying a snippet toggles its button to Copied", async ({ browser }) => {
    const context = await browser.newContext({
      extraHTTPHeaders: { "x-blindfold-identity": "alice" },
      permissions: ["clipboard-write"],
    });
    const page = await context.newPage();
    await page.goto("/ui/connect");
    const btn = page
      .getByTestId("connect-card-openai-sdk")
      .getByTestId("copyable-snippet-btn")
      .first();

    await expect(btn).not.toContainText("Copied");
    await btn.click();
    await expect(btn).toContainText("Copied");
    await context.close();
  });

  test("the sidebar Connect item and Home's Connect card both navigate to /connect", async ({
    alicePage,
  }) => {
    await alicePage.goto("/ui/status");
    const sidebar = alicePage.getByRole("navigation", { name: "Management navigation" });
    await sidebar.getByRole("link", { name: "Connect" }).click();
    await expect(alicePage).toHaveURL(/\/ui\/connect$/);

    await alicePage.goto("/ui/status");
    const card = alicePage.getByTestId("home-connect-card");
    await expect(card).toHaveAttribute("href", "/ui/connect");
    await card.click();
    await expect(alicePage).toHaveURL(/\/ui\/connect$/);
  });
});

test.describe("Connect page — SPA-side privacy properties", () => {
  // authorized-only re-identification and audit-on-decrypt: N/A, see file header.

  test("egress hygiene: zero non-loopback requests, and no re-identification (reveal) call is ever made", async ({
    alicePage,
    baseURL,
  }) => {
    const requests: string[] = [];
    alicePage.on("request", (req) => requests.push(req.url()));

    await alicePage.goto("/ui/connect");
    await expect(alicePage.getByTestId("connect-card-codex")).toBeVisible();

    const firstPartyHost = new URL(baseURL!).host;
    const thirdParty = requests.filter((url) => new URL(url).host !== firstPartyHost);
    expect(thirdParty, `unexpected non-loopback requests: ${thirdParty.join(", ")}`).toEqual([]);

    // The shell chrome wrapping every route (workspace switcher, audit-drawer
    // badge) makes its own authorized-viewer-scoped /v1/management/workspaces
    // and /v1/management/audit reads regardless of which route is active --
    // that's expected shell behavior, not something Connect.tsx itself
    // triggers, and neither call is a reveal. The property this page's own
    // code must satisfy is narrower: it never calls the one endpoint that
    // performs re-identification (GET /v1/management/surrogate/{surrogate}/real),
    // matching its complete absence of any decrypt/reveal UI affordance.
    const revealCalls = requests.filter((url) =>
      /\/v1\/management\/surrogate\/[^/]+\/real/.test(new URL(url).pathname)
    );
    expect(
      revealCalls,
      "Connect renders no reveal/decrypt affordance -- it must never call the re-identification endpoint"
    ).toEqual([]);
  });
});
