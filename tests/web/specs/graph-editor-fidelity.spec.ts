import { test, expect, WORKSPACE, PERSON_SURROGATE, ORG3_SURROGATE } from "./fixtures";

// Graph editor comp fidelity (issue #112). Raises /ui/graph to the settled comp
// (Claude Design project 1b6e3a05-9854-4edd-bde5-9e422210854e, "Blindfold
// Management.dc.html") without changing any behavior #98/#56 already proved —
// this spec only asserts the presentational gaps the issue calls out:
//   - page header + subtitle (previously absent)
//   - mono node/edge labels, dual-encoded shape+color (round+blue person,
//     square+purple term) on the Cytoscape canvas
//   - inspector reveal is the ochre "Reveal & log" full-width action
//
// No privacy property changes here (reveal gating, audit-on-reveal, closed-world
// restore) — those are proved unchanged by the pre-existing graph-editor-shell
// and org-graph specs, left untouched.

test.describe("graph editor — header", () => {
  test("renders the 'Graph editor' heading and subtitle", async ({ alicePage }) => {
    const page = alicePage;
    await page.goto(`/ui/graph?workspace=${WORKSPACE}`);

    await expect(page.getByRole("heading", { name: "Graph editor" })).toBeVisible();
    await expect(page.getByTestId("graph-editor-subtitle")).toHaveText(
      "Click a node to inspect it. Person = round, term = square — kind is dual-encoded by shape and color."
    );
  });
});

test.describe("graph editor — canvas fidelity", () => {
  test("node and edge labels are mono; person = round + blue, term = square + purple", async ({
    alicePage,
  }) => {
    const page = alicePage;
    await page.goto(`/ui/graph?workspace=${WORKSPACE}`);
    await page.waitForFunction(
      () =>
        (window as any).__blindfoldGraph &&
        (window as any).__blindfoldGraph.nodes().length > 0
    );

    const style = await page.evaluate(
      ([personLabel, termLabel]: [string, string]) => {
        const cy = (window as any).__blindfoldGraph;
        const person = cy.nodes().filter((n: any) => n.data("label") === personLabel)[0];
        const term = cy.nodes().filter((n: any) => n.data("label") === termLabel)[0];
        const edge = cy.edges()[0];
        return {
          personShape: person.style("shape"),
          personColor: person.style("background-color"),
          personFont: person.style("font-family"),
          termShape: term.style("shape"),
          termColor: term.style("background-color"),
          termFont: term.style("font-family"),
          edgeFont: edge ? edge.style("font-family") : null,
        };
      },
      // ORG3 (not ORG_SURROGATE) — reserved for graph-editor-shell, entity-list-shell
      // renames ORG_SURROGATE away, which would leave this test's term-node lookup empty.
      [PERSON_SURROGATE, ORG3_SURROGATE]
    );

    expect(style.personShape).toBe("ellipse");
    expect(style.personFont).toMatch(/mono/i);
    expect(style.termShape).toMatch(/rectangle/);
    expect(style.termFont).toMatch(/mono/i);
    expect(style.edgeFont).toMatch(/mono/i);
    // Dual-encoded color: person blue (--bf-person), term purple (--bf-term)
    expect(style.personColor).not.toBe(style.termColor);
  });
});

test.describe("graph editor — inspector relationships", () => {
  test("relationship rows render as mono chips", async ({ alicePage }) => {
    const page = alicePage;
    await page.goto(`/ui/graph?workspace=${WORKSPACE}`);
    await page.waitForFunction(
      () =>
        (window as any).__blindfoldGraph &&
        (window as any).__blindfoldGraph.nodes().length > 0
    );
    const point = await page.evaluate((label: string) => {
      const cy = (window as any).__blindfoldGraph;
      const node = cy.nodes().filter((n: any) => n.data("label") === label)[0];
      const rp = node.renderedPosition();
      const rect = document.getElementById("cy")!.getBoundingClientRect();
      return { x: rect.left + rp.x, y: rect.top + rp.y };
    }, PERSON_SURROGATE);
    await page.mouse.click(point.x, point.y);

    const inspector = page.getByTestId("graph-inspector");
    await expect(inspector).toBeVisible();
    const chip = inspector.locator(".bf-graph-inspector-edge").first();
    await expect(chip).toBeVisible();
    await expect(chip).toHaveCSS("border-radius", "999px");
    const chipText = chip.locator(".bf-graph-inspector-edge-text");
    await expect(chipText).toHaveCSS("font-family", /mono/i);
  });
});

test.describe("graph editor — inspector reveal", () => {
  test("reveal is a bottom, full-width, ochre 'Reveal & log' action", async ({ alicePage }) => {
    const page = alicePage;
    await page.goto(`/ui/graph?workspace=${WORKSPACE}`);
    await page.waitForFunction(
      () =>
        (window as any).__blindfoldGraph &&
        (window as any).__blindfoldGraph.nodes().length > 0
    );
    const point = await page.evaluate((label: string) => {
      const cy = (window as any).__blindfoldGraph;
      const node = cy.nodes().filter((n: any) => n.data("label") === label)[0];
      const rp = node.renderedPosition();
      const rect = document.getElementById("cy")!.getBoundingClientRect();
      return { x: rect.left + rp.x, y: rect.top + rp.y };
    }, PERSON_SURROGATE);
    await page.mouse.click(point.x, point.y);

    const inspector = page.getByTestId("graph-inspector");
    await expect(inspector).toBeVisible();

    const revealBtn = inspector.getByTestId("reveal-btn");
    await expect(revealBtn).toHaveText("Reveal & log");
    await expect(revealBtn).toHaveCSS("background-color", "rgb(176, 127, 32)"); // --bf-ochre
    await expect(revealBtn).toHaveClass(/bf-reveal-badge--full/);
  });
});
