import {
  test,
  expect,
  WORKSPACE,
  REAL_PERSON,
  PERSON_SURROGATE,
  PERSON2_SURROGATE,
  PERSON3_SURROGATE,
  ORG_SURROGATE,
  ORG2_SURROGATE,
  ORG3_SURROGATE,
  auditEventsFor,
  rowByCurrentSurrogate,
} from "./fixtures";

// Entity list migrated into the shell (issue #97). Behavior authority: the settled
// entity-list design (docs/design/entity-list-view-design-brief.md, ADR-0016/17/18)
// and the shipped /ui/entity-list behavior (tests/web/specs/entity-list.spec.ts,
// left untouched — the legacy embedded page is retired by a later slice, ADR-0026).
// This spec drives the new /ui/entities shell route against the same running server.

test.describe("entity list shell — subtitle (issue #111)", () => {
  test("states the entity count, workspace slug, and that variations stay hidden", async ({
    alicePage,
  }) => {
    await alicePage.goto(`/ui/entities`);
    await alicePage.getByTestId("entity-table").waitFor();
    const rowCount = await alicePage.locator('[data-testid^="entity-row-"]').count();
    await expect(alicePage.getByTestId("entity-list-subtitle")).toHaveText(
      `${rowCount} entities in ${WORKSPACE}. Variations stay hidden — reachable only ` +
        `through real-name search and the merge dialog.`
    );
  });
});

test.describe("entity list shell — table & filters", () => {
  test("renders both kinds with dual-encoded kind marks", async ({ alicePage }) => {
    await alicePage.goto(`/ui/entities`);
    const personRow = await rowByCurrentSurrogate(alicePage, PERSON_SURROGATE);
    const termRow = await rowByCurrentSurrogate(alicePage, ORG_SURROGATE);
    await expect(personRow.locator(".bf-kind-mark--person")).toBeVisible();
    await expect(termRow.locator(".bf-kind-mark--term")).toBeVisible();
  });

  test("kind filter narrows to the selected kind only", async ({ alicePage }) => {
    await alicePage.goto(`/ui/entities`);
    await alicePage.getByTestId("kind-filter").selectOption("term");
    await expect(alicePage.locator("tr", { hasText: PERSON_SURROGATE })).toHaveCount(0);
    await expect(alicePage.locator("tr", { hasText: ORG_SURROGATE })).toBeVisible();
  });

  test("surrogate free-text filter narrows client-side instantly", async ({ alicePage }) => {
    await alicePage.goto(`/ui/entities`);
    await alicePage.getByTestId("surrogate-filter").fill(PERSON2_SURROGATE);
    await expect(alicePage.locator("tr", { hasText: PERSON2_SURROGATE })).toBeVisible();
    await expect(alicePage.locator("tr", { hasText: PERSON_SURROGATE }).filter({
      hasNotText: PERSON2_SURROGATE,
    })).toHaveCount(0);
  });

  test("clicking the surrogate header toggles sort order", async ({ alicePage }) => {
    await alicePage.goto(`/ui/entities`);
    const surrogateCells = () => alicePage.locator('[data-testid^="surrogate-text-"]');
    await alicePage.getByTestId("sort-surrogate").click();
    const ascFirst = await surrogateCells().first().textContent();
    await alicePage.getByTestId("sort-surrogate").click();
    const descFirst = await surrogateCells().first().textContent();
    expect(ascFirst).not.toEqual(descFirst);
  });
});

test.describe("entity list shell — workspace access", () => {
  test("an identity with no role anywhere sees no workspace, never an error", async ({
    bobPage,
  }) => {
    await bobPage.goto(`/ui/entities`);
    await expect(bobPage.locator("body")).toContainText("No workspace selected");
  });
});

test.describe("entity list shell — real-name search", () => {
  test("locked without re-identifier: no input, structural curation still visible", async ({
    davePage,
  }) => {
    await davePage.goto(`/ui/entities`);
    await davePage.getByTestId("search-mode-real-name").click();
    await expect(davePage.getByTestId("real-name-search-locked")).toBeVisible();
    await expect(davePage.getByTestId("real-name-input")).toHaveCount(0);
    // Structural curation (rename) stays available without re-identifier — the
    // surrogate cell is a live edit trigger, not hidden or disabled.
    const row = davePage.locator("tr", { hasText: PERSON_SURROGATE });
    await expect(row.locator('[data-testid^="surrogate-text-"]')).toBeVisible();
    await expect(row.locator('[data-testid^="merge-trigger-"]')).toBeVisible();
  });

  test("authorized: renders the ochre 'Look up & log' button and the blind-index helper line", async ({
    alicePage,
  }) => {
    await alicePage.goto(`/ui/entities`);
    await alicePage.getByTestId("search-mode-real-name").click();

    const btn = alicePage.getByTestId("real-name-search-btn");
    await expect(btn).toHaveText("Look up & log");
    await expect(btn).toHaveClass(/bf-btn-ochre/);
    await expect(alicePage.getByTestId("real-name-input")).toHaveClass(/bf-toolbar-input--ochre/);
    await expect(alicePage.locator(".bf-real-name-hint")).toHaveText(
      "Blind-index equality — no free-text fishing. The lookup itself is an audit event."
    );
  });

  test("authorized: exact real-name hit highlights every matching surrogate row and audits", async ({
    alicePage,
    baseURL,
  }) => {
    await alicePage.goto(`/ui/entities`);
    await alicePage.getByTestId("search-mode-real-name").click();
    await alicePage.getByTestId("real-name-input").fill(REAL_PERSON);
    await alicePage.getByTestId("real-name-search-btn").click();

    // REAL_PERSON is the planted duplicate — both surrogate rows must highlight.
    await expect(
      alicePage.locator("tr", { hasText: PERSON_SURROGATE })
    ).toHaveClass(/bf-row-highlighted/);
    await expect(
      alicePage.locator("tr", { hasText: PERSON2_SURROGATE })
    ).toHaveClass(/bf-row-highlighted/);

    const hits = await auditEventsFor(baseURL!, "entity-list-searched", "alice");
    expect(hits.some((r) => r.reason.includes("hit_count=2"))).toBe(true);
  });

  test("a miss is honest and still audited", async ({ alicePage, baseURL }) => {
    await alicePage.goto(`/ui/entities`);
    await alicePage.getByTestId("search-mode-real-name").click();
    await alicePage.getByTestId("real-name-input").fill("Nobody Here");
    await alicePage.getByTestId("real-name-search-btn").click();

    await expect(alicePage.getByTestId("search-message")).toContainText(
      "No exact match in this workspace"
    );
    const misses = await auditEventsFor(baseURL!, "entity-list-searched", "alice");
    expect(misses.some((r) => r.reason.includes("hit_count=0"))).toBe(true);
  });
});

test.describe("entity list shell — reveal", () => {
  test("authorized reveal: confirm dialog, transient real: chip, audited", async ({
    alicePage,
    baseURL,
  }) => {
    await alicePage.goto(`/ui/entities`);
    const row = alicePage.locator("tr", { hasText: PERSON_SURROGATE });
    await row.getByTestId("reveal-btn").click();
    await row.getByTestId("reveal-confirm").click();

    await expect(row.getByTestId("reveal-value")).toHaveText(`real: ${REAL_PERSON}`);
    const reveals = await auditEventsFor(baseURL!, "re-identified", "alice");
    expect(reveals.length).toBeGreaterThan(0);
  });

  test("authorized: per-row Reveal renders as an ochre button (bg/border/ink) with a lock icon, never a gray pill (issue #111 hard rule)", async ({
    alicePage,
  }) => {
    await alicePage.goto(`/ui/entities`);
    const row = alicePage.locator("tr", { hasText: PERSON_SURROGATE });
    const revealBtn = row.getByTestId("reveal-btn");
    await expect(revealBtn).toHaveClass(/bf-reveal-badge--ochre/);
    await expect(revealBtn.locator("svg")).toBeVisible();
  });

  test("authorized reveal: confirm dialog matches the shared comp — ochre top border, lock badge, audit-attribution copy, 'Reveal & log' button (issue #111)", async ({
    alicePage,
  }) => {
    await alicePage.goto(`/ui/entities`);
    const row = alicePage.locator("tr", { hasText: PERSON_SURROGATE });
    await row.getByTestId("reveal-btn").click();

    const dialog = row.getByRole("dialog", { name: "Confirm reveal" });
    await expect(dialog).toHaveClass(/bf-reveal-confirm--ochre/);
    await expect(dialog.getByTestId("reveal-confirm-badge").locator("svg")).toBeVisible();
    await expect(dialog.locator("p")).toHaveText(
      "Revealing the real value will be recorded as an audit event attributed to you."
    );
    const confirmBtn = dialog.getByTestId("reveal-confirm");
    await expect(confirmBtn).toHaveText("Reveal & log");
    await expect(confirmBtn).toHaveClass(/bf-btn-ochre/);
  });

  test("unauthorized (curator only): locked, never fires, real value never shown", async ({
    davePage,
  }) => {
    const requestUrls: string[] = [];
    davePage.on("request", (req) => requestUrls.push(req.url()));
    await davePage.goto(`/ui/entities`);
    const row = davePage.locator("tr", { hasText: PERSON_SURROGATE });
    await expect(row.getByTestId("reveal-locked")).toBeVisible();
    await expect(davePage.locator("body")).not.toContainText(REAL_PERSON);
    expect(requestUrls.some((u) => u.includes("/v1/management/surrogate/"))).toBe(false);
  });
});

test.describe("entity list shell — inline rename", () => {
  test("collision is a hard reject with a red inline error", async ({ alicePage }) => {
    await alicePage.goto(`/ui/entities`);
    const row = await rowByCurrentSurrogate(alicePage, PERSON_SURROGATE);
    await row.locator('[data-testid^="surrogate-text-"]').click();
    const input = row.locator('[data-testid^="rename-input-"]');
    await input.fill(PERSON2_SURROGATE);
    await row.locator('[data-testid^="rename-save-"]').click();

    const error = row.locator('[data-testid^="rename-error-"]');
    await expect(error).toContainText("Collision");
    await expect(input).toHaveClass(/bf-surrogate-input--error/);
  });

  test("dependent rename is a soft warn requiring acknowledge before it commits", async ({
    alicePage,
  }) => {
    await alicePage.goto(`/ui/entities`);
    const row = await rowByCurrentSurrogate(alicePage, ORG_SURROGATE);
    await row.locator('[data-testid^="surrogate-text-"]').click();
    const input = row.locator('[data-testid^="rename-input-"]');
    await input.fill("Cascadia Partners");
    await row.locator('[data-testid^="rename-save-"]').click();

    const warn = row.locator('[data-testid^="rename-warn-"]');
    await expect(warn).toBeVisible();
    const ackSave = row.locator('[data-testid^="rename-ack-save-"]');
    await expect(ackSave).toBeDisabled();

    await warn.locator('input[type="checkbox"]').check();
    await expect(ackSave).toBeEnabled();
    await ackSave.click();

    await expect(row.locator('[data-testid^="surrogate-text-"]')).toHaveText(
      "Cascadia Partners"
    );
  });
});

test.describe("entity list shell — edge chips", () => {
  test("delete removes the chip", async ({ alicePage }) => {
    await alicePage.goto(`/ui/entities`);
    const row = alicePage.locator("tr", { hasText: PERSON2_SURROGATE });
    const chip = row.locator(".bf-edge-chip").first();
    await expect(chip).toBeVisible();
    await chip.locator('[data-testid^="edge-chip-delete-"]').click();
    await expect(row.locator(".bf-edge-chip")).toHaveCount(0);
  });

  test("chips are kind-colored to their target and mono-encode relation and target separately (issue #111 polish)", async ({
    alicePage,
  }) => {
    await alicePage.goto(`/ui/entities`);
    // PERSON3/ORG3 are the graph-editor-shell's exclusive fixtures — entity-list-shell
    // specs never rename them, so (unlike PERSON_SURROGATE/ORG_SURROGATE, mutated by
    // the inline-rename tests above) their surrogate stays stable within this file.
    const row = alicePage.locator("tr", { hasText: PERSON3_SURROGATE });
    const chip = row.locator(".bf-edge-chip").first();
    // Both employer and subsidiary_of always target a term entity — the chip's
    // dual-encoding color follows the actual target_kind, not a hardcoded person tint.
    await expect(chip).toHaveClass(/bf-edge-chip--term/);
    await expect(chip.getByTestId(/edge-chip-relation-/)).toHaveText("employer");
    await expect(chip.getByTestId(/edge-chip-target-/)).toHaveText(ORG3_SURROGATE);
  });

  test("re-target is kind-constrained (term only) and applies delete+create", async ({
    alicePage,
  }) => {
    await alicePage.goto(`/ui/entities`);
    const row = alicePage.locator("tr", { hasText: PERSON_SURROGATE });
    const chip = row.locator(".bf-edge-chip").first();
    await chip.locator('[data-testid^="edge-chip-retarget-"]').first().click();
    const select = chip.locator('select[data-testid^="edge-chip-retarget-select-"]');
    await select.selectOption({ label: ORG2_SURROGATE });
    await chip.locator('[data-testid^="edge-chip-retarget-apply-"]').click();

    await expect(row.locator(".bf-edge-chip-label")).toContainText(ORG2_SURROGATE);
  });
});

test.describe("entity list shell — merge", () => {
  test("per-row Merge entry opens a same-kind candidate picker and confirm dialog", async ({
    alicePage,
    baseURL,
  }) => {
    await alicePage.goto(`/ui/entities`);
    const row = alicePage.locator("tr", { hasText: PERSON_SURROGATE });
    await row.locator('[data-testid^="merge-trigger-"]').click();
    const picker = row.locator('[data-testid^="merge-picker-"]').first();
    await picker.locator("select").selectOption({ label: PERSON2_SURROGATE });
    await picker.locator('[data-testid^="merge-picker-start-"]').click();

    const dialog = alicePage.getByTestId("merge-dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByTestId("merge-card-survivor")).toContainText(PERSON_SURROGATE);
    await expect(dialog.getByTestId("merge-card-retired")).toContainText(PERSON2_SURROGATE);

    // Swap flips which card is which — no meaning attached to click order.
    await dialog.getByTestId("merge-swap").click();
    await expect(dialog.getByTestId("merge-card-survivor")).toContainText(PERSON2_SURROGATE);
    await dialog.getByTestId("merge-swap").click();

    await dialog.getByTestId("merge-confirm").click();
    await expect(dialog).toBeHidden();
    await expect(alicePage.locator("tr", { hasText: PERSON2_SURROGATE })).toHaveCount(0);

    const merges = await auditEventsFor(baseURL!, "entity-merged", "alice");
    expect(merges.length).toBeGreaterThan(0);
  });
});

test.describe("entity list shell — variations nowhere except lookup/merge", () => {
  test("the default table never renders a Variations column", async ({ alicePage }) => {
    await alicePage.goto(`/ui/entities`);
    await expect(alicePage.getByTestId("entity-table")).not.toContainText("Variations");
  });
});

test.describe("entity list shell — polish (issue #111)", () => {
  test("card radius is 14px and the header row is uppercase on a muted #fafbfc background", async ({
    alicePage,
  }) => {
    await alicePage.goto(`/ui/entities`);
    const card = alicePage.locator(".bf-entity-list");
    await expect(card).toHaveCSS("border-radius", "14px");

    const headerRow = alicePage.locator(".bf-entity-table thead tr");
    await expect(headerRow).toHaveCSS("background-color", "rgb(250, 251, 252)");
    const firstHeader = alicePage.getByTestId("sort-surrogate");
    await expect(firstHeader).toHaveCSS("text-transform", "uppercase");
  });
});

test.describe("entity list shell — density preference", () => {
  test("Settings -> Preferences density persists on the device and drives row padding", async ({
    alicePage,
  }) => {
    await alicePage.goto(`/ui/settings`);
    await alicePage.getByTestId("density-option-comfortable").click();
    await expect(alicePage.getByTestId("density-option-comfortable")).toHaveAttribute(
      "aria-checked",
      "true"
    );

    await alicePage.goto(`/ui/entities`);
    await expect(alicePage.locator(".bf-entity-list")).toHaveAttribute(
      "data-density",
      "comfortable"
    );

    // Persists across a full reload (localStorage, not in-memory React state).
    await alicePage.reload();
    await expect(alicePage.locator(".bf-entity-list")).toHaveAttribute(
      "data-density",
      "comfortable"
    );
  });
});
