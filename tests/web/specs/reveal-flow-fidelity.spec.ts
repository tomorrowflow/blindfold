import {
  test,
  expect,
  WORKSPACE,
  REAL_PERSON,
  PERSON_SURROGATE,
} from "./fixtures";
import { clickGraphNode } from "./fixtures";

// Reveal flow glitches (issue #178). RevealButton.tsx / shell.css's shared
// confirm dialog + revealed value/error surfaces regressed on two fronts,
// observed live after the #173-#177 comp-fidelity run:
//   1. `.bf-reveal-confirm` is a `position:absolute; top:100%` popover with no
//      viewport-collision handling — clipped off-screen (right edge on the
//      entity list, bottom edge + trigger-overlap on the graph inspector's
//      bottom `--full` variant).
//   2. The revealed `real:` value / error render as unbounded inline text
//      inside a `white-space:nowrap` Actions cell — long content forces the
//      cell (and so the whole row) wider, misaligning the entity-list grid.
//
// Presentation only — reveal gating, audit-on-reveal, and closed-world
// restore are unchanged and proved by the pre-existing entity-list-shell /
// graph-editor-shell / org-graph reveal specs, left untouched.

function boxesOverlap(
  a: { x: number; y: number; width: number; height: number },
  b: { x: number; y: number; width: number; height: number }
) {
  return a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
}

function fullyWithinViewport(
  box: { x: number; y: number; width: number; height: number },
  viewport: { width: number; height: number }
) {
  return box.x >= 0 && box.y >= 0 && box.x + box.width <= viewport.width && box.y + box.height <= viewport.height;
}

test.describe("reveal flow — confirm dialog viewport containment (issue #178)", () => {
  test("entity list: confirm dialog for the first row stays fully within the viewport and never overlaps its trigger", async ({
    alicePage,
  }) => {
    const page = alicePage;
    await page.goto(`/ui/entities`);
    const row = page.locator('[data-testid^="entity-row-"]').first();
    const trigger = row.getByTestId("reveal-btn");
    await trigger.click();

    const dialog = page.getByRole("dialog", { name: "Confirm reveal" });
    await expect(dialog).toBeVisible();

    const viewport = page.viewportSize()!;
    const dialogBox = (await dialog.boundingBox())!;
    const triggerBox = (await trigger.boundingBox())!;

    expect(fullyWithinViewport(dialogBox, viewport)).toBe(true);
    expect(boxesOverlap(dialogBox, triggerBox)).toBe(false);
  });

  test("entity list: confirm dialog for the last row (right-edge Actions column) stays fully within the viewport and never overlaps its trigger", async ({
    alicePage,
  }) => {
    const page = alicePage;
    await page.goto(`/ui/entities`);
    const row = page.locator('[data-testid^="entity-row-"]').last();
    const trigger = row.getByTestId("reveal-btn");
    await trigger.scrollIntoViewIfNeeded();
    await trigger.click();

    const dialog = page.getByRole("dialog", { name: "Confirm reveal" });
    await expect(dialog).toBeVisible();

    const viewport = page.viewportSize()!;
    const dialogBox = (await dialog.boundingBox())!;
    const triggerBox = (await trigger.boundingBox())!;

    expect(fullyWithinViewport(dialogBox, viewport)).toBe(true);
    expect(boxesOverlap(dialogBox, triggerBox)).toBe(false);
  });

  test("graph inspector: confirm dialog for the bottom 'Reveal & log' button stays fully within the viewport and never overlaps its trigger", async ({
    alicePage,
  }) => {
    const page = alicePage;
    await page.goto(`/ui/graph?workspace=${WORKSPACE}`);
    await clickGraphNode(page, PERSON_SURROGATE);

    const inspector = page.getByTestId("graph-inspector");
    await expect(inspector).toBeVisible();
    const trigger = inspector.getByTestId("reveal-btn");
    await trigger.click();

    const dialog = page.getByRole("dialog", { name: "Confirm reveal" });
    await expect(dialog).toBeVisible();

    const viewport = page.viewportSize()!;
    const dialogBox = (await dialog.boundingBox())!;
    const triggerBox = (await trigger.boundingBox())!;

    expect(fullyWithinViewport(dialogBox, viewport)).toBe(true);
    expect(boxesOverlap(dialogBox, triggerBox)).toBe(false);
  });
});

test.describe("reveal flow — revealed value / error containment (issue #178)", () => {
  test("entity list: a long revealed real value stays visually bounded and never widens the Actions cell", async ({
    alicePage,
  }) => {
    const page = alicePage;
    const longReal = "A Very Long Real Name That Would Otherwise Overflow The Actions Column";
    await page.route("**/v1/management/surrogate/*/real", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ real: longReal }) })
    );
    await page.goto(`/ui/entities`);
    const table = page.getByTestId("entity-table");
    const tableBoxBefore = (await table.boundingBox())!;

    const row = page.locator("tr", { hasText: PERSON_SURROGATE });
    await row.getByTestId("reveal-btn").click();
    await row.getByTestId("reveal-confirm").click();

    const chip = row.getByTestId("reveal-value");
    await expect(chip).toHaveText(`real: ${longReal}`);

    const chipBox = (await chip.boundingBox())!;
    const tableBoxAfter = (await table.boundingBox())!;

    // The chip never pushes the table wider than it was before the reveal...
    expect(tableBoxAfter.width).toBeLessThanOrEqual(tableBoxBefore.width + 1);
    // ...and stays inside the table's own right edge, never spilling out.
    expect(chipBox.x + chipBox.width).toBeLessThanOrEqual(tableBoxAfter.x + tableBoxAfter.width + 1);
  });

  test("entity list: a reveal error renders bounded and never widens the Actions cell", async ({ alicePage }) => {
    const page = alicePage;
    const detail =
      "surrogate not found in this workspace after searching every known mapping and retired entry available";
    await page.route("**/v1/management/surrogate/*/real", (route) =>
      route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail }) })
    );
    await page.goto(`/ui/entities`);
    const table = page.getByTestId("entity-table");
    const tableBoxBefore = (await table.boundingBox())!;

    const row = page.locator("tr", { hasText: PERSON_SURROGATE });
    await row.getByTestId("reveal-btn").click();
    await row.getByTestId("reveal-confirm").click();

    const errorEl = row.locator(".bf-reveal-error");
    await expect(errorEl).toHaveText(detail);

    const tableBoxAfter = (await table.boundingBox())!;
    expect(tableBoxAfter.width).toBeLessThanOrEqual(tableBoxBefore.width + 1);
  });
});
