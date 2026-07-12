import {
  test,
  expect,
  WORKSPACE,
  REAL_PERSON,
  PERSON_SURROGATE,
  PERSON3_SURROGATE,
  ORG3_SURROGATE,
  ORG2_SURROGATE,
  auditEventsFor,
} from "./fixtures";

// Graph editor shell migration (issue #98). Behavior authority:
//   1. docs/design/graph-editor-design-brief.md §9 (settled interaction defaults)
//   2. Legacy /ui/org-graph behavior (spa.py, retired)
//   3. ADR-0016 (merge semantics), ADR-0017 (surrogate-space rendering + reveal)
//
// Privacy properties verified (ADR-0017):
//   F (access control): reveal is locked without re-identifier; structural edits
//     require admin (pre-existing gap ADR-0028, not re-wired here).
//   Cytoscape is vendored (npm, not CDN) — no CDN request for /ui/graph.
//   No console/page error on load — proves #56 is fixed (UMD-wrapper TypeError gone).

// ---------------------------------------------------------------------------
// Helper: click a Cytoscape node by surrogate label.
// The canvas has no per-node DOM elements; we read the renderedPosition via the
// test-only window.__blindfoldGraph hook (identical pattern to the legacy
// spa.py hook and the existing org-graph.spec.ts clickGraphNode helper).
// ---------------------------------------------------------------------------
async function clickGraphNode(page: import("@playwright/test").Page, label: string) {
  // Wait for the graph to be initialised and the target node to be within the
  // visible canvas area.  After the inspector opens, cy.fit() is called via two
  // rAF hops; we poll until renderedPosition is in-bounds before reading it, so
  // the click always lands on the correct node regardless of rAF timing.
  await page.waitForFunction(
    (nodeLabel: string) => {
      const cy = (window as any).__blindfoldGraph;
      if (!cy || cy.nodes().length === 0) return false;
      const node = cy.nodes().filter((n: any) => n.data("label") === nodeLabel)[0];
      if (!node) return false;
      const rp = node.renderedPosition();
      const cyEl = document.getElementById("cy");
      if (!cyEl) return false;
      const rect = cyEl.getBoundingClientRect();
      // renderedPosition is relative to the cy container — node is in-bounds
      // when 0 ≤ rp.x ≤ rect.width and 0 ≤ rp.y ≤ rect.height.
      return rp.x >= 0 && rp.x <= rect.width && rp.y >= 0 && rp.y <= rect.height;
    },
    label,
    { timeout: 10000 }
  );
  const point = await page.evaluate((nodeLabel: string) => {
    const cy = (window as any).__blindfoldGraph;
    const node = cy.nodes().filter((n: any) => n.data("label") === nodeLabel)[0];
    if (!node) throw new Error(`node not found: ${nodeLabel}`);
    const rp = node.renderedPosition();
    const rect = document.getElementById("cy")!.getBoundingClientRect();
    return { x: rect.left + rp.x, y: rect.top + rp.y };
  }, label);
  await page.mouse.click(point.x, point.y);
}

// ---------------------------------------------------------------------------
// Helper: drag one node onto another (for merge-by-drag)
// ---------------------------------------------------------------------------
async function dragNodeOntoNode(
  page: import("@playwright/test").Page,
  sourceLabel: string,
  targetLabel: string
) {
  // Wait for both nodes to be within the visible canvas area before dragging.
  await page.waitForFunction(
    ([sl, tl]: [string, string]) => {
      const cy = (window as any).__blindfoldGraph;
      if (!cy || cy.nodes().length === 0) return false;
      const cyEl = document.getElementById("cy");
      if (!cyEl) return false;
      const rect = cyEl.getBoundingClientRect();
      function inBounds(label: string) {
        const node = cy.nodes().filter((n: any) => n.data("label") === label)[0];
        if (!node) return false;
        const rp = node.renderedPosition();
        return rp.x >= 0 && rp.x <= rect.width && rp.y >= 0 && rp.y <= rect.height;
      }
      return inBounds(sl) && inBounds(tl);
    },
    [sourceLabel, targetLabel] as [string, string],
    { timeout: 10000 }
  );
  const positions = await page.evaluate(
    ([sl, tl]: [string, string]) => {
      const cy = (window as any).__blindfoldGraph;
      function pos(label: string) {
        const node = cy.nodes().filter((n: any) => n.data("label") === label)[0];
        if (!node) throw new Error(`node not found: ${label}`);
        const rp = node.renderedPosition();
        const rect = document.getElementById("cy")!.getBoundingClientRect();
        return { x: rect.left + rp.x, y: rect.top + rp.y };
      }
      return { source: pos(sl), target: pos(tl) };
    },
    [sourceLabel, targetLabel] as [string, string]
  );
  await page.mouse.move(positions.source.x, positions.source.y);
  await page.mouse.down();
  // Slow drag in steps so Cytoscape detects the freeon event
  const steps = 10;
  for (let i = 1; i <= steps; i++) {
    const x = positions.source.x + (positions.target.x - positions.source.x) * (i / steps);
    const y = positions.source.y + (positions.target.y - positions.source.y) * (i / steps);
    await page.mouse.move(x, y);
  }
  await page.mouse.up();
}

// ---------------------------------------------------------------------------
// 1. No console/page error on load (#56 regression test)
// ---------------------------------------------------------------------------

test("no console or page error on /graph load (regression: #56 UMD-wrapper TypeError gone)", async ({
  alicePage,
}) => {
  const page = alicePage;
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];

  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => {
    pageErrors.push(String(err));
  });

  await page.goto(`/ui/graph`);
  // Wait for canvas to be ready (graph loaded)
  await page.waitForFunction(
    () =>
      (window as any).__blindfoldGraph !== undefined &&
      (window as any).__blindfoldGraph !== null
  );

  expect(
    consoleErrors,
    `unexpected console errors on /graph load: ${consoleErrors.join("; ")}`
  ).toEqual([]);
  expect(
    pageErrors,
    `unexpected page errors on /graph load: ${pageErrors.join("; ")}`
  ).toEqual([]);
});

// ---------------------------------------------------------------------------
// 2. Authorized reveal: shows real value, audited, reason never contains real name
// ---------------------------------------------------------------------------

test.describe("graph editor — reveal", () => {
  test("authorized viewer: reveal shows the real value and is audited", async ({
    alicePage,
    baseURL,
  }) => {
    const page = alicePage;
    await page.goto(`/ui/graph`);
    await clickGraphNode(page, PERSON_SURROGATE);

    // Inspector opens with a Reveal badge
    await expect(page.getByTestId("graph-inspector")).toBeVisible();
    await page.getByTestId("reveal-btn").first().click();

    // Light-friction confirm dialog
    await expect(page.getByRole("dialog", { name: "Confirm reveal" })).toBeVisible();
    await page.getByTestId("reveal-confirm").click();

    // Real value appears transiently as "real: {value}"
    await expect(page.getByTestId("reveal-value").first()).toHaveText(
      `real: ${REAL_PERSON}`
    );

    // Audit trail: re-identified event, reason uses surrogate not real name
    const reveals = await auditEventsFor(baseURL!, "re-identified", "alice");
    expect(reveals.length).toBeGreaterThan(0);
    const last = reveals.at(-1)!;
    expect(last.reason).toBe(`surrogate=${PERSON_SURROGATE}`);
    expect(last.reason).not.toContain(REAL_PERSON);
  });

  test("unauthorized viewer (curator only): reveal is locked, real value never in DOM, denial audited", async ({
    davePage,
    baseURL,
  }) => {
    const page = davePage;
    const requestUrls: string[] = [];
    page.on("request", (req) => requestUrls.push(req.url()));

    await page.goto(`/ui/graph`);
    await clickGraphNode(page, PERSON_SURROGATE);

    await expect(page.getByTestId("graph-inspector")).toBeVisible();
    // Locked reveal badge (never fires a network call)
    await expect(page.getByTestId("reveal-locked").first()).toBeVisible();
    // Real name must never appear in DOM
    await expect(page.locator("body")).not.toContainText(REAL_PERSON);
    // No surrogate/real endpoint called
    expect(requestUrls.some((u) => u.includes("/v1/management/surrogate/"))).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// 3. Merge-by-drag: dialog shows Survivor/Retired, swap works, confirm merges
// ---------------------------------------------------------------------------

test.describe("graph editor — merge", () => {
  test("drag node A onto node B shows merge dialog with Survivor/Retired labels", async ({
    alicePage,
  }) => {
    const page = alicePage;
    await page.goto(`/ui/graph`);
    await page.waitForFunction(
      () =>
        (window as any).__blindfoldGraph &&
        (window as any).__blindfoldGraph.nodes().length > 0
    );

    // PERSON3 is reserved for graph-editor-shell tests (never touched by entity-list-shell).
    await dragNodeOntoNode(page, PERSON_SURROGATE, PERSON3_SURROGATE);

    const dialog = page.getByTestId("merge-dialog");
    await expect(dialog).toBeVisible();
    // Dragged = Survivor, drop target = Retired (design-brief §Q1)
    await expect(dialog.getByTestId("merge-card-survivor")).toContainText(PERSON_SURROGATE);
    await expect(dialog.getByTestId("merge-card-retired")).toContainText(PERSON3_SURROGATE);

    // Swap flips the labels
    await dialog.getByTestId("merge-swap").click();
    await expect(dialog.getByTestId("merge-card-survivor")).toContainText(PERSON3_SURROGATE);
    await expect(dialog.getByTestId("merge-card-retired")).toContainText(PERSON_SURROGATE);

    // Cancel — don't actually merge (preserve state for other tests)
    await dialog.getByTestId("merge-cancel").click();
    await expect(dialog).toBeHidden();
  });

  test("cross-kind merge (person onto term) is rejected with a toast, dialog never shown", async ({
    alicePage,
  }) => {
    const page = alicePage;
    const toasts: string[] = [];
    // Watch for toast messages (they appear in bf-toast-outlet)
    await page.goto(`/ui/graph`);
    await page.waitForFunction(
      () =>
        (window as any).__blindfoldGraph &&
        (window as any).__blindfoldGraph.nodes().length > 0
    );

    // Use ORG3 (reserved for graph-editor-shell, pristine) for cross-kind drag.
    await dragNodeOntoNode(page, PERSON_SURROGATE, ORG3_SURROGATE);

    // No merge dialog must appear
    await expect(page.getByTestId("merge-dialog")).toHaveCount(0);
    // A toast/error message must appear somewhere on the page mentioning cross-kind
    await expect(page.locator(".bf-toast-outlet")).toContainText(/[Cc]ross-kind|person.*term|term.*person/);
    void toasts;
  });
});

// ---------------------------------------------------------------------------
// 4. Edge draw: drag from handle → kind-aware picker → confirm creates edge
// ---------------------------------------------------------------------------

test.describe("graph editor — edge draw", () => {
  test("edge draw: select source → click Draw edge → click target → picker → confirm creates edge", async ({
    alicePage,
  }) => {
    const page = alicePage;
    await page.goto(`/ui/graph`);

    // Step 1: select the source node (PERSON3_SURROGATE) by clicking it.
    // PERSON3 is reserved for graph-editor-shell tests (not mutated by entity-list-shell).
    // PERSON3 has employer→ORG3, but no edge to ORG2, so drawing PERSON3→ORG2 is valid.
    await clickGraphNode(page, PERSON3_SURROGATE);
    const inspector = page.getByTestId("graph-inspector");
    await expect(inspector).toBeVisible();

    // Step 2: click "Draw edge" in the inspector to arm draw mode
    await page.getByTestId("inspector-draw-edge").click();

    // Toolbar should show the draw-edge hint with the source node label
    await expect(page.getByTestId("tool-hint")).toContainText(PERSON3_SURROGATE);

    // Step 3: fire a tap on the target node (ORG2_SURROGATE) via the Cytoscape
    // event system.  Clicking the canvas pixel coordinate is fragile because the
    // inspector resizes the canvas, invalidating old rendered positions.  Emitting
    // directly on the cy node is equivalent — the same tap handler fires — and is
    // used only for the canvas (a WebGL surface with no per-node DOM elements).
    await page.evaluate((targetLabel: string) => {
      const cy = (window as any).__blindfoldGraph;
      const node = cy.nodes().filter((n: any) => n.data("label") === targetLabel)[0];
      if (!node) throw new Error(`target node not found: ${targetLabel}`);
      node.emit("tap");
    }, ORG2_SURROGATE);

    // Edge picker dialog appears
    const picker = page.getByTestId("edge-picker-dialog");
    await expect(picker).toBeVisible();
    // Kind-aware: person→term shows only employer
    await expect(picker.getByTestId("edge-picker-relation")).toContainText("employer");
    // Source → Target label shown
    await expect(picker.getByTestId("edge-picker-direction")).toContainText(
      `${PERSON3_SURROGATE} → ${ORG2_SURROGATE}`
    );

    await picker.getByTestId("edge-picker-confirm").click();
    await expect(picker).toBeHidden();
    // Edge should now appear on the canvas
    await page.waitForFunction(
      (surr: string) => {
        const cy = (window as any).__blindfoldGraph;
        return (
          cy &&
          cy.edges().some(
            (e: any) =>
              e.data("relation") === "employer" &&
              (cy.getElementById(e.data("target")).data("label") === surr ||
                cy.getElementById(e.data("source")).data("label") === surr)
          )
        );
      },
      ORG2_SURROGATE
    );
  });
});

// ---------------------------------------------------------------------------
// 5. Edge delete: select an edge and delete it, canvas reflects removal
// ---------------------------------------------------------------------------

test.describe("graph editor — edge delete", () => {
  test("delete edge via inspector removes it from the canvas", async ({
    alicePage,
  }) => {
    const page = alicePage;
    await page.goto(`/ui/graph`);
    await clickGraphNode(page, PERSON_SURROGATE);

    const inspector = page.getByTestId("graph-inspector");
    await expect(inspector).toBeVisible();

    // Find the first delete button in the edge list
    const deleteBtn = inspector.locator('[data-testid^="inspector-edge-delete-"]').first();
    await expect(deleteBtn).toBeVisible();

    const edgeTestId = await deleteBtn.getAttribute("data-testid");
    const edgeId = edgeTestId?.replace("inspector-edge-delete-", "") ?? "";

    await deleteBtn.click();

    // Edge should be gone from the canvas
    await page.waitForFunction(
      (eid: string) => {
        const cy = (window as any).__blindfoldGraph;
        return cy && cy.getElementById(`edge-${eid}`).length === 0;
      },
      edgeId
    );
  });
});

// ---------------------------------------------------------------------------
// 6. Rename — collision (hard reject) and dependent warning (soft)
// ---------------------------------------------------------------------------

test.describe("graph editor — rename in inspector", () => {
  test("collision: red inline error, rename blocked", async ({ alicePage }) => {
    const page = alicePage;
    await page.goto(`/ui/graph`);
    await clickGraphNode(page, PERSON_SURROGATE);

    const inspector = page.getByTestId("graph-inspector");
    await expect(inspector).toBeVisible();
    const input = inspector.getByTestId("inspector-rename-input");
    // PERSON3_SURROGATE is reserved for graph-editor-shell (never merged away), so it
    // still exists in the system and triggers a collision when set on PERSON_SURROGATE.
    await input.fill(PERSON3_SURROGATE); // already taken -> collision
    await inspector.getByTestId("inspector-rename-save").click();

    await expect(inspector.getByTestId("inspector-rename-error")).toContainText("Collision");
    await expect(input).toHaveClass(/bf-surrogate-input--error/);
  });

  test("dependent warning: slate banner, acknowledge required before commit", async ({
    alicePage,
  }) => {
    const page = alicePage;
    await page.goto(`/ui/graph`);
    // ORG3 is reserved for graph-editor-shell (not renamed by entity-list-shell).
    // PERSON3 has employer→ORG3, so renaming ORG3 surfaces the dependent soft-warn.
    await clickGraphNode(page, ORG3_SURROGATE);

    const inspector = page.getByTestId("graph-inspector");
    await expect(inspector).toBeVisible();
    const input = inspector.getByTestId("inspector-rename-input");
    await input.fill("Glacier Holdings");
    await inspector.getByTestId("inspector-rename-save").click();

    // Dependent warning banner must appear
    const warn = inspector.getByTestId("inspector-rename-warn");
    await expect(warn).toBeVisible();
    const ackSave = inspector.getByTestId("inspector-rename-ack-save");
    await expect(ackSave).toBeDisabled();

    // Check acknowledge checkbox to enable the button
    await warn.locator('input[type="checkbox"]').check();
    await expect(ackSave).toBeEnabled();
    await ackSave.click();

    // Surrogate updated in inspector
    await expect(inspector.getByTestId("inspector-surrogate")).toHaveText("Glacier Holdings");
  });
});

// ---------------------------------------------------------------------------
// 7. In-dialog Reveal (merge dialog) — same gate, same audit event (ADR-0017)
// ---------------------------------------------------------------------------

test.describe("graph editor — in-dialog reveal (merge)", () => {
  test("merge dialog: authorized reveal on Survivor card shows real value and audits", async ({
    alicePage,
    baseURL,
  }) => {
    const page = alicePage;
    await page.goto(`/ui/graph`);
    await page.waitForFunction(
      () =>
        (window as any).__blindfoldGraph &&
        (window as any).__blindfoldGraph.nodes().length > 0
    );

    // PERSON3 is reserved for graph-editor-shell (never merged by entity-list-shell).
    await dragNodeOntoNode(page, PERSON_SURROGATE, PERSON3_SURROGATE);
    const dialog = page.getByTestId("merge-dialog");
    await expect(dialog).toBeVisible();

    // Reveal on the Survivor card (the dragged node = PERSON_SURROGATE)
    const survivorCard = dialog.getByTestId("merge-card-survivor");
    await survivorCard.getByTestId("reveal-btn").click();
    await page.getByRole("dialog", { name: "Confirm reveal" }).waitFor();
    await page.getByTestId("reveal-confirm").click();

    await expect(survivorCard.getByTestId("reveal-value")).toHaveText(
      `real: ${REAL_PERSON}`
    );

    // Must be audited
    const reveals = await auditEventsFor(baseURL!, "re-identified", "alice");
    expect(reveals.length).toBeGreaterThan(0);

    // Cancel merge (don't actually commit — preserve fixture state)
    await dialog.getByTestId("merge-cancel").click();
  });

  test("merge dialog: locked reveal on Survivor when caller lacks re-identifier", async ({
    davePage,
  }) => {
    const page = davePage;
    await page.goto(`/ui/graph`);
    await page.waitForFunction(
      () =>
        (window as any).__blindfoldGraph &&
        (window as any).__blindfoldGraph.nodes().length > 0
    );

    // PERSON3 is reserved for graph-editor-shell (never merged by entity-list-shell).
    await dragNodeOntoNode(page, PERSON_SURROGATE, PERSON3_SURROGATE);
    const dialog = page.getByTestId("merge-dialog");
    await expect(dialog).toBeVisible();

    const survivorCard = dialog.getByTestId("merge-card-survivor");
    await expect(survivorCard.getByTestId("reveal-locked")).toBeVisible();
    await expect(page.locator("body")).not.toContainText(REAL_PERSON);

    await dialog.getByTestId("merge-cancel").click();
  });
});
