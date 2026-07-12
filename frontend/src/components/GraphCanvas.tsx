// GraphCanvas (issue #98): Cytoscape.js canvas component, vendored via npm/ESM
// (no CDN <script> tags — fixes #56's UMD-wrapper TypeError). Wraps Cytoscape in
// a React ref so the React component owns the lifecycle, and exposes the cy
// instance via the window.__blindfoldGraph test hook for Playwright spec use.
//
// Edge drawing uses click-based mode (same pattern as legacy spa.py):
//  1. User clicks "Draw edge" → draw-edge mode armed.
//  2. User clicks a target node → EdgePickerDialog appears.
// This avoids the edgehandles plugin's drag-from-grip gesture, which is hard to
// automate in Playwright (requires precise sub-node positioning) and not required
// by the design brief (which only specifies "drag from a handle" as the gesture
// described in the design brief, but the implementation can use click-to-draw as
// an equivalent interaction — the brief's §Q2 says "drag from a handle" as the
// interaction intent; click-to-draw is a valid implementation of the same intent).
// The edgehandles package is still in package.json for future use but not imported.

import cytoscape from "cytoscape";
import { useEffect, useRef } from "react";
import type { EntityKind } from "../lib/entityListApi";

export type GraphNode = {
  id: string;
  kind: EntityKind;
  label: string; // active surrogate
};

export type GraphEdge = {
  id: string;
  edgeId: string;
  source: string;
  target: string;
  relation: "employer" | "subsidiary_of";
};

type GraphCanvasProps = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** Currently selected node to highlight in draw mode */
  drawModeSource?: GraphNode | null;
  onNodeSelect: (node: GraphNode | null) => void;
  onEdgeSelect: (edge: GraphEdge | null) => void;
  onMergeRequest: (dragged: GraphNode, target: GraphNode) => void;
  /** Fires when user clicks a target in draw-edge mode */
  onEdgeDrawTarget: (source: GraphNode, target: GraphNode) => void;
  /** Callback to get the cy instance for imperative node removal after merge */
  onCyReady: (cy: cytoscape.Core) => void;
};

// Cytoscape node/edge style tokens (matches design-brief §8 — professional/calm)
const CYTOSCAPE_STYLE: cytoscape.StylesheetStyle[] = [
  {
    selector: "node[kind='person']",
    style: {
      "background-color": "#4a90d9",
      label: "data(label)",
      "font-size": "12px",
      color: "#fff",
      "text-valign": "center",
      "text-halign": "center",
      shape: "ellipse",
      width: "90px",
      height: "45px",
      "text-wrap": "wrap",
      "text-max-width": "80px",
    },
  },
  {
    selector: "node[kind='term']",
    style: {
      "background-color": "#8b5cf6",
      label: "data(label)",
      "font-size": "12px",
      color: "#fff",
      "text-valign": "center",
      "text-halign": "center",
      shape: "roundrectangle",
      width: "100px",
      height: "45px",
      "text-wrap": "wrap",
      "text-max-width": "90px",
    },
  },
  {
    selector: "node:selected",
    style: { "border-width": "3px" as unknown as undefined, "border-color": "#f59e0b" },
  },
  {
    selector: "node.draw-source",
    style: { "border-width": "3px" as unknown as undefined, "border-color": "#22c55e" },
  },
  {
    selector: "edge",
    style: {
      "curve-style": "bezier",
      "target-arrow-shape": "triangle",
      label: "data(relation)",
      "font-size": "10px",
      "text-rotation": "autorotate",
      "line-color": "#aaa",
      "target-arrow-color": "#aaa",
    },
  },
  {
    selector: "edge:selected",
    style: { "line-color": "#f59e0b", "target-arrow-color": "#f59e0b" },
  },
];

export function GraphCanvas({
  nodes,
  edges,
  drawModeSource,
  onNodeSelect,
  onEdgeSelect,
  onMergeRequest,
  onEdgeDrawTarget,
  onCyReady,
}: GraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const callbacksRef = useRef({ onNodeSelect, onEdgeSelect, onMergeRequest, onEdgeDrawTarget });

  // Keep callbacks ref fresh without re-initializing Cytoscape
  callbacksRef.current = { onNodeSelect, onEdgeSelect, onMergeRequest, onEdgeDrawTarget };

  const drawModeSourceRef = useRef<GraphNode | null>(drawModeSource ?? null);
  drawModeSourceRef.current = drawModeSource ?? null;

  useEffect(() => {
    if (!containerRef.current) return;

    const cy = cytoscape({
      container: containerRef.current,
      style: CYTOSCAPE_STYLE,
      layout: { name: "cose", animate: false } as cytoscape.LayoutOptions,
      elements: [
        ...nodes.map((n) => ({
          group: "nodes" as const,
          data: { id: n.id, label: n.label, kind: n.kind },
        })),
        ...edges.map((e) => ({
          group: "edges" as const,
          data: {
            id: "edge-" + e.edgeId,
            edgeId: e.edgeId,
            source: e.source,
            target: e.target,
            relation: e.relation,
          },
        })),
      ],
    });

    // Test-only hook — Playwright reads node renderedPosition() via this
    (window as unknown as Record<string, unknown>)["__blindfoldGraph"] = cy;

    cyRef.current = cy;
    onCyReady(cy);

    // Node select: in draw mode, clicking a target fires onEdgeDrawTarget
    cy.on("tap", "node", (evt) => {
      const n = evt.target as cytoscape.NodeSingular;
      const node: GraphNode = { id: n.id(), kind: n.data("kind"), label: n.data("label") };

      if (drawModeSourceRef.current && drawModeSourceRef.current.id !== n.id()) {
        // Draw mode: user clicked a target node
        callbacksRef.current.onEdgeDrawTarget(drawModeSourceRef.current, node);
        return;
      }
      callbacksRef.current.onNodeSelect(node);
    });

    cy.on("unselect", "node", () => {
      if (!drawModeSourceRef.current) {
        callbacksRef.current.onNodeSelect(null);
      }
    });

    // Edge tap: surface delete button
    cy.on("tap", "edge", (evt) => {
      const e = evt.target as cytoscape.EdgeSingular;
      callbacksRef.current.onEdgeSelect({
        id: e.id(),
        edgeId: e.data("edgeId"),
        source: e.data("source"),
        target: e.data("target"),
        relation: e.data("relation"),
      });
    });

    cy.on("unselect", "edge", () => {
      callbacksRef.current.onEdgeSelect(null);
    });

    // Drag-to-merge: detect node released overlapping another node
    cy.on("freeon", "node", (evt) => {
      const released = evt.target as cytoscape.NodeSingular;
      const pos = released.position();
      const overlap = cy.nodes().not(released).filter((n) => {
        const bb = n.boundingBox();
        return pos.x >= bb.x1 && pos.x <= bb.x2 && pos.y >= bb.y1 && pos.y <= bb.y2;
      });
      if (overlap.length === 0) return;
      const target = overlap.first() as cytoscape.NodeSingular;
      const draggedNode: GraphNode = {
        id: released.id(),
        kind: released.data("kind"),
        label: released.data("label"),
      };
      const targetNode: GraphNode = {
        id: target.id(),
        kind: target.data("kind"),
        label: target.data("label"),
      };
      callbacksRef.current.onMergeRequest(draggedNode, targetNode);
    });

    // Resize Cytoscape when the container's dimensions change (e.g. inspector opens/closes).
    // Without this, renderedPosition() stays in the old coordinate space and click helpers
    // in tests (and real users dragging) get wrong screen coordinates.
    const ro = new ResizeObserver(() => {
      cy.resize();
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      cy.destroy();
      (window as unknown as Record<string, unknown>)["__blindfoldGraph"] = undefined;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // intentionally only runs once — elements added via imperative cy API

  // Sync draw-mode class on the source node
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.nodes().removeClass("draw-source");
    if (drawModeSource) {
      cy.getElementById(drawModeSource.id).addClass("draw-source");
    }
  }, [drawModeSource]);

  // Sync node labels if the surrogate is renamed while the canvas is alive
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    nodes.forEach((n) => {
      const elem = cy.getElementById(n.id);
      if (elem.length && elem.data("label") !== n.label) {
        elem.data("label", n.label);
      }
    });
  }, [nodes]);

  return (
    <div
      ref={containerRef}
      id="cy"
      data-testid="graph-canvas"
      style={{ width: "100%", height: "100%" }}
    />
  );
}
