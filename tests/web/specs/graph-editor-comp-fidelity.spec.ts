import { test, expect, WORKSPACE, PERSON_SURROGATE, ORG3_SURROGATE } from "./fixtures";

// Graph editor comp fidelity remainder (issue #174). Covers the three concrete
// gaps deferred by issue #112:
//   1. Inspector kind indicator: must be dot + separate label, not text in the dot.
//   2. Canvas card framing: bordered, rounded, dotted-grid, 520px tall.
//   3. Node sizing: compact chips sized to label content, no clipping or overlap.
//
// No privacy-property changes — leak-audit clauses A–G are N/A for this
// presentation-fidelity slice (no request path touched).

// ---------------------------------------------------------------------------
// Helper: click a Cytoscape node by surrogate label (same pattern as shell spec)
// ---------------------------------------------------------------------------
async function clickGraphNode(page: import("@playwright/test").Page, label: string) {
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
// 1. Inspector kind indicator: colored dot + separate readable kind label
// ---------------------------------------------------------------------------

test.describe("graph editor — inspector kind indicator (issue #174)", () => {
  test("person entity: kind row shows empty colored dot + separate 'person' label text", async ({
    alicePage,
  }) => {
    const page = alicePage;
    await page.goto(`/ui/graph?workspace=${WORKSPACE}`);
    await clickGraphNode(page, PERSON_SURROGATE);

    const inspector = page.getByTestId("graph-inspector");
    await expect(inspector).toBeVisible();

    // The dot span must be empty (no text content) — text goes in the label span
    const dot = inspector.locator(".bf-kind-mark").first();
    await expect(dot).toBeVisible();
    await expect(dot).toHaveText("");

    // A separate label span must carry the kind text
    const kindLabel = inspector.locator(".bf-kind-label").first();
    await expect(kindLabel).toBeVisible();
    await expect(kindLabel).toHaveText("person");
  });

  test("term entity: kind row shows empty colored dot + separate 'term' label text", async ({
    alicePage,
  }) => {
    const page = alicePage;
    await page.goto(`/ui/graph?workspace=${WORKSPACE}`);
    await clickGraphNode(page, ORG3_SURROGATE);

    const inspector = page.getByTestId("graph-inspector");
    await expect(inspector).toBeVisible();

    const dot = inspector.locator(".bf-kind-mark").first();
    await expect(dot).toBeVisible();
    await expect(dot).toHaveText("");

    const kindLabel = inspector.locator(".bf-kind-label").first();
    await expect(kindLabel).toBeVisible();
    await expect(kindLabel).toHaveText("term");
  });

  test("inspector-kind testid is preserved on the kind row wrapper", async ({
    alicePage,
  }) => {
    const page = alicePage;
    await page.goto(`/ui/graph?workspace=${WORKSPACE}`);
    await clickGraphNode(page, PERSON_SURROGATE);

    const inspector = page.getByTestId("graph-inspector");
    await expect(inspector).toBeVisible();

    // data-testid="inspector-kind" must still exist and be findable
    const kindRow = inspector.getByTestId("inspector-kind");
    await expect(kindRow).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// 2. Canvas card framing: border, radius, height, dotted-grid background
// ---------------------------------------------------------------------------

test.describe("graph editor — canvas card framing (issue #174)", () => {
  test("canvas has 1px border and 14px border-radius (card framing)", async ({
    alicePage,
  }) => {
    const page = alicePage;
    await page.goto(`/ui/graph?workspace=${WORKSPACE}`);
    await page.waitForFunction(
      () =>
        (window as any).__blindfoldGraph !== undefined &&
        (window as any).__blindfoldGraph !== null
    );

    const canvas = page.getByTestId("graph-canvas");
    await expect(canvas).toHaveCSS("border-style", "solid");
    await expect(canvas).toHaveCSS("border-width", "1px");
    await expect(canvas).toHaveCSS("border-radius", "14px");
  });

  test("canvas is 520px tall", async ({ alicePage }) => {
    const page = alicePage;
    await page.goto(`/ui/graph?workspace=${WORKSPACE}`);
    await page.waitForFunction(
      () =>
        (window as any).__blindfoldGraph !== undefined &&
        (window as any).__blindfoldGraph !== null
    );

    const canvas = page.getByTestId("graph-canvas");
    await expect(canvas).toHaveCSS("height", "520px");
  });

  test("canvas has dotted-grid background-image pattern", async ({ alicePage }) => {
    const page = alicePage;
    await page.goto(`/ui/graph?workspace=${WORKSPACE}`);
    await page.waitForFunction(
      () =>
        (window as any).__blindfoldGraph !== undefined &&
        (window as any).__blindfoldGraph !== null
    );

    const canvas = page.getByTestId("graph-canvas");
    // The radial-gradient background encodes the dotted-grid pattern
    const bgImage = await canvas.evaluate((el) => getComputedStyle(el).backgroundImage);
    expect(bgImage).toMatch(/radial-gradient/);
  });

  test("canvas background-size is 22px 22px (grid spacing)", async ({ alicePage }) => {
    const page = alicePage;
    await page.goto(`/ui/graph?workspace=${WORKSPACE}`);
    await page.waitForFunction(
      () =>
        (window as any).__blindfoldGraph !== undefined &&
        (window as any).__blindfoldGraph !== null
    );

    const canvas = page.getByTestId("graph-canvas");
    await expect(canvas).toHaveCSS("background-size", "22px 22px");
  });
});

// ---------------------------------------------------------------------------
// 3. Node sizing: label-width chips, no overlap between nodes
// ---------------------------------------------------------------------------

test.describe("graph editor — node sizing to label content (issue #174)", () => {
  test("person node width is not fixed at 90px — expands with label", async ({
    alicePage,
  }) => {
    const page = alicePage;
    await page.goto(`/ui/graph?workspace=${WORKSPACE}`);
    await page.waitForFunction(
      () =>
        (window as any).__blindfoldGraph &&
        (window as any).__blindfoldGraph.nodes().length > 0
    );

    // Verify person node width is 'label' (auto-sized) not a hardcoded pixel value
    const personStyleWidth = await page.evaluate((label: string) => {
      const cy = (window as any).__blindfoldGraph;
      const node = cy.nodes().filter((n: any) => n.data("label") === label)[0];
      if (!node) throw new Error(`node not found: ${label}`);
      // cy.style() string value reflects the stylesheet — mapData produces 'label'
      return node.pstyle("width").strValue;
    }, PERSON_SURROGATE);
    expect(personStyleWidth).toBe("label");
  });

  test("term node width is not fixed at 100px — expands with label", async ({
    alicePage,
  }) => {
    const page = alicePage;
    await page.goto(`/ui/graph?workspace=${WORKSPACE}`);
    await page.waitForFunction(
      () =>
        (window as any).__blindfoldGraph &&
        (window as any).__blindfoldGraph.nodes().length > 0
    );

    const termStyleWidth = await page.evaluate((label: string) => {
      const cy = (window as any).__blindfoldGraph;
      const node = cy.nodes().filter((n: any) => n.data("label") === label)[0];
      if (!node) throw new Error(`node not found: ${label}`);
      return node.pstyle("width").strValue;
    }, ORG3_SURROGATE);
    expect(termStyleWidth).toBe("label");
  });

  test("no two nodes have bounding boxes that meaningfully overlap after layout", async ({
    alicePage,
  }) => {
    const page = alicePage;
    await page.goto(`/ui/graph?workspace=${WORKSPACE}`);
    // Wait for layout to complete (cose is synchronous with animate:false, but
    // still poll until stable node count)
    await page.waitForFunction(
      () =>
        (window as any).__blindfoldGraph &&
        (window as any).__blindfoldGraph.nodes().length > 0
    );

    // Tolerance: cose is a force-directed layout and may leave nodes with a tiny
    // bounding-box overlap due to floating-point convergence. We allow up to 4px
    // of overlap in each axis (well within rendering rounding) but reject any
    // meaningful collision (>4px in BOTH axes simultaneously = visual overlap).
    const OVERLAP_TOLERANCE_PX = 4;
    const overlaps = await page.evaluate((tol: number) => {
      const cy = (window as any).__blindfoldGraph;
      const nodes = cy.nodes().toArray();
      const overlaps: Array<[string, string, number, number]> = [];
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i].boundingBox();
          const b = nodes[j].boundingBox();
          // Compute how many pixels they overlap in each axis
          const xOverlap = Math.min(a.x2, b.x2) - Math.max(a.x1, b.x1);
          const yOverlap = Math.min(a.y2, b.y2) - Math.max(a.y1, b.y1);
          if (xOverlap > tol && yOverlap > tol) {
            overlaps.push([nodes[i].data("label"), nodes[j].data("label"), xOverlap, yOverlap]);
          }
        }
      }
      return overlaps;
    }, OVERLAP_TOLERANCE_PX);
    expect(
      overlaps,
      `Node bounding boxes meaningfully overlap (>${OVERLAP_TOLERANCE_PX}px): ${JSON.stringify(overlaps)}`
    ).toEqual([]);
  });

  test("person node shape is still ellipse after label-sizing change", async ({
    alicePage,
  }) => {
    const page = alicePage;
    await page.goto(`/ui/graph?workspace=${WORKSPACE}`);
    await page.waitForFunction(
      () =>
        (window as any).__blindfoldGraph &&
        (window as any).__blindfoldGraph.nodes().length > 0
    );

    const shape = await page.evaluate((label: string) => {
      const cy = (window as any).__blindfoldGraph;
      const node = cy.nodes().filter((n: any) => n.data("label") === label)[0];
      return node.style("shape");
    }, PERSON_SURROGATE);
    expect(shape).toBe("ellipse");
  });

  test("term node shape is still roundrectangle after label-sizing change", async ({
    alicePage,
  }) => {
    const page = alicePage;
    await page.goto(`/ui/graph?workspace=${WORKSPACE}`);
    await page.waitForFunction(
      () =>
        (window as any).__blindfoldGraph &&
        (window as any).__blindfoldGraph.nodes().length > 0
    );

    const shape = await page.evaluate((label: string) => {
      const cy = (window as any).__blindfoldGraph;
      const node = cy.nodes().filter((n: any) => n.data("label") === label)[0];
      return node.style("shape");
    }, ORG3_SURROGATE);
    expect(shape).toMatch(/rectangle/);
  });
});
