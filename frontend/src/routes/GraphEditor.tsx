// GraphEditor (issue #98): org-graph editor migrated into the unified shell.
// Behavior authority (in priority order):
//   1. docs/design/graph-editor-design-brief.md §9 (settled interaction defaults)
//   2. Legacy /ui/org-graph (spa.py _ORG_GRAPH_HTML) — feature-for-feature
//   3. ADR-0016 (merge semantics), ADR-0017 (surrogate-space rendering + reveal)
//
// Key invariants (ADR-0017):
// - Graph renders in surrogate-space by default — no audit event on page load.
// - Reveal is the only audited action, per-node, gated by re-identifier role.
// - Cytoscape is imported via ESM (npm), never a CDN <script> tag (fixes #56).

import { useCallback, useEffect, useRef, useState } from "react";
import type cytoscape from "cytoscape";
import { useWorkspace } from "../components/WorkspaceContext";
import { useToast } from "../components/ToastContext";
import { GraphCanvas } from "../components/GraphCanvas";
import type { GraphNode, GraphEdge } from "../components/GraphCanvas";
import { GraphInspector } from "../components/GraphInspector";
import { MergeDialog } from "../components/MergeDialog";
import { EdgePickerDialog } from "../components/EdgePickerDialog";
import type { EdgePickerNode } from "../components/EdgePickerDialog";
import {
  fetchGraph,
  fetchEntities,
  type EntityListRow,
  type GraphNodeData,
  type GraphEdgeData,
} from "../lib/entityListApi";

type ToolMode = "select" | "draw-edge" | "merge";

const TOOL_LABELS: Record<ToolMode, string> = {
  select: "Select",
  "draw-edge": "Draw edge",
  merge: "Merge",
};

// Single source of truth for both the segmented-control tab title and the
// toolbar hint line (issue #112: the two had drifted out of sync).
const TOOL_HINTS: Record<ToolMode, string> = {
  select: "Click a node to inspect it. Drag onto another same-kind node to merge.",
  "draw-edge": "Select a node first, then click Draw edge in the inspector, then click a target node.",
  merge: "Drag one node onto another same-kind node to merge them.",
};

export function GraphEditor() {
  const { activeWorkspace } = useWorkspace();
  const { toast } = useToast();
  const workspace = activeWorkspace?.slug ?? null;
  const canReveal = activeWorkspace?.roles.includes("re-identifier") ?? false;

  const [graphNodes, setGraphNodes] = useState<GraphNodeData[]>([]);
  const [graphEdges, setGraphEdges] = useState<GraphEdgeData[]>([]);
  const [entityRows, setEntityRows] = useState<EntityListRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [toolMode, setToolMode] = useState<ToolMode>("select");
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  // draw-edge mode: the source node for the pending edge
  const [drawSource, setDrawSource] = useState<GraphNode | null>(null);
  const [mergePair, setMergePair] = useState<{ dragged: EntityListRow; target: EntityListRow } | null>(null);
  const [edgePicker, setEdgePicker] = useState<{ source: EdgePickerNode; target: EdgePickerNode } | null>(null);

  const cyRef = useRef<cytoscape.Core | null>(null);

  useEffect(() => {
    if (!workspace) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    Promise.all([fetchGraph(workspace), fetchEntities(workspace)])
      .then(([graph, entities]) => {
        if (cancelled) return;
        setGraphNodes(graph.nodes);
        setGraphEdges(graph.edges);
        setEntityRows(entities);
      })
      .catch((e) => {
        if (!cancelled) setLoadError(String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workspace]);

  const handleCyReady = useCallback((cy: cytoscape.Core) => {
    cyRef.current = cy;
  }, []);

  // Re-fit the canvas when the inspector opens or closes so that node rendered
  // positions stay within the (now smaller/larger) container. Without this,
  // nodes near the right edge of the original full-width canvas would have
  // stale renderedPosition() coords pointing into the inspector panel.
  useEffect(() => {
    // Two rAF hops let the browser fully paint the flex layout change before we
    // call cy.resize() + cy.fit(), so the container's new bounding rect is stable.
    let rafId: number;
    const outer = window.requestAnimationFrame(() => {
      rafId = window.requestAnimationFrame(() => {
        if (cyRef.current) {
          cyRef.current.resize();
          cyRef.current.fit(undefined, 24);
        }
      });
    });
    return () => {
      window.cancelAnimationFrame(outer);
      window.cancelAnimationFrame(rafId);
    };
  }, [selectedNode]);

  // Convert GraphNode (canvas) → EntityListRow (inspector) by entity_id lookup
  function rowForNode(node: GraphNode): EntityListRow | null {
    return entityRows.find((r) => r.entity_id === node.id) ?? null;
  }

  function handleNodeSelect(node: GraphNode | null) {
    if (toolMode === "draw-edge" && drawSource) {
      // In draw mode, selecting a node is the "target" action → handled by onEdgeDrawTarget
      return;
    }
    setSelectedNode(node);
    if (!node) setSelectedEdgeId(null);
  }

  function handleEdgeSelect(edge: GraphEdge | null) {
    setSelectedEdgeId(edge?.edgeId ?? null);
  }

  function handleMergeRequest(dragged: GraphNode, target: GraphNode) {
    // Same-kind only (person↔person, term↔term) — cross-kind rejected with toast
    if (dragged.kind !== target.kind) {
      toast(`Cross-kind merge is not supported (${dragged.kind}↔${target.kind}). Only person↔person or term↔term.`);
      return;
    }
    const draggedRow = rowForNode(dragged);
    const targetRow = rowForNode(target);
    if (!draggedRow || !targetRow) {
      toast("Could not find entity data for merge candidates. Try reloading.");
      return;
    }
    // Dragged node = default winner (Survivor), drop target = Retired (design-brief §Q1)
    setMergePair({ dragged: draggedRow, target: targetRow });
  }

  function handleEdgeDrawTarget(source: EdgePickerNode, target: EdgePickerNode) {
    setDrawSource(null);
    setToolMode("select");
    setEdgePicker({ source, target });
  }

  function handleMerged(loserId: string) {
    // Remove the loser node from the canvas imperatively
    if (cyRef.current) {
      cyRef.current.getElementById(loserId).remove();
    }
    setGraphNodes((nodes) => nodes.filter((n) => n.id !== loserId));
    setEntityRows((rows) => rows.filter((r) => r.entity_id !== loserId));
    setMergePair(null);
    setSelectedNode(null);
  }

  function handleRenamed(entityId: string, newSurrogate: string) {
    setGraphNodes((nodes) =>
      nodes.map((n) => (n.id === entityId ? { ...n, label: newSurrogate } : n))
    );
    setEntityRows((rows) =>
      rows.map((r) =>
        r.entity_id === entityId ? { ...r, active_surrogate: newSurrogate } : r
      )
    );
  }

  function handleEdgeDeleted(edgeId: string) {
    if (cyRef.current) {
      cyRef.current.getElementById(`edge-${edgeId}`).remove();
    }
    setGraphEdges((edges) => edges.filter((e) => e.id !== edgeId));
    setEntityRows((rows) =>
      rows.map((r) => ({
        ...r,
        edges: r.edges.filter((e) => e.edge_id !== edgeId),
      }))
    );
    if (selectedEdgeId === edgeId) setSelectedEdgeId(null);
  }

  function handleEdgeCreated(
    edgeId: string,
    sourceId: string,
    targetId: string,
    relation: "employer" | "subsidiary_of"
  ) {
    if (cyRef.current) {
      cyRef.current.add({
        group: "edges",
        data: {
          id: `edge-${edgeId}`,
          edgeId,
          source: sourceId,
          target: targetId,
          relation,
        },
      });
    }
    const newEdge: GraphEdgeData = { id: edgeId, source: sourceId, target: targetId, relation };
    setGraphEdges((edges) => [...edges, newEdge]);
    setEdgePicker(null);
  }

  // "Draw edge" button in inspector arms draw mode with the currently selected node
  function handleDrawEdgeClick() {
    if (!selectedNode) return;
    setDrawSource(selectedNode);
    setToolMode("draw-edge");
    toast(`Draw edge from ${selectedNode.label}: click a target node.`);
  }

  function handleToolMode(mode: ToolMode) {
    setToolMode(mode);
    if (mode !== "draw-edge") {
      setDrawSource(null);
    }
  }

  const canvasNodes: GraphNode[] = graphNodes.map((n) => ({
    id: n.id,
    kind: n.kind,
    label: n.label,
  }));

  const canvasEdges: GraphEdge[] = graphEdges.map((e) => ({
    id: `edge-${e.id}`,
    edgeId: e.id,
    source: e.source,
    target: e.target,
    relation: e.relation,
  }));

  if (!workspace) {
    return (
      <div className="bf-graph-editor" data-testid="graph-editor">
        <p className="bf-empty">No workspace selected.</p>
      </div>
    );
  }

  const inspectorRow = selectedNode ? rowForNode(selectedNode) : null;

  return (
    <div className="bf-graph-editor" data-testid="graph-editor">
      <div className="bf-graph-header">
        <h1>Graph editor</h1>
        <p className="bf-card-subtitle" data-testid="graph-editor-subtitle">
          Click a node to inspect it. Person = round, term = square — kind is dual-encoded by shape and color.
        </p>
      </div>

      {/* Tool switcher (segmented, per issue brief) */}
      <div className="bf-graph-toolbar" data-testid="graph-toolbar">
        <div
          className="bf-search-mode-toggle"
          role="tablist"
          aria-label="Graph tool"
        >
          {(["select", "draw-edge", "merge"] as ToolMode[]).map((mode) => (
            <button
              key={mode}
              type="button"
              role="tab"
              aria-selected={toolMode === mode}
              className={`bf-search-mode-option${toolMode === mode ? " bf-search-mode-option--active" : ""}`}
              onClick={() => handleToolMode(mode)}
              data-testid={`tool-${mode}`}
              title={TOOL_HINTS[mode]}
            >
              {TOOL_LABELS[mode]}
            </button>
          ))}
        </div>
        <span className="bf-graph-tool-hint" data-testid="tool-hint">
          {toolMode === "draw-edge" && drawSource
            ? `Drawing edge from ${drawSource.label} — click a target node.`
            : TOOL_HINTS[toolMode]}
        </span>
        {loadError && <span className="bf-error" data-testid="graph-load-error">{loadError}</span>}
      </div>

      <div className="bf-graph-main">
        {loading && <p className="bf-empty" style={{ padding: "24px" }}>Loading…</p>}

        {!loading && (
          <GraphCanvas
            nodes={canvasNodes}
            edges={canvasEdges}
            drawModeSource={drawSource}
            onNodeSelect={handleNodeSelect}
            onEdgeSelect={handleEdgeSelect}
            onMergeRequest={handleMergeRequest}
            onEdgeDrawTarget={handleEdgeDrawTarget}
            onCyReady={handleCyReady}
          />
        )}

        {inspectorRow && (
          <GraphInspector
            key={inspectorRow.entity_id}
            workspace={workspace}
            row={inspectorRow}
            canReveal={canReveal}
            onClose={() => { setSelectedNode(null); setDrawSource(null); setToolMode("select"); }}
            onRenamed={handleRenamed}
            onEdgeDeleted={handleEdgeDeleted}
            onDrawEdgeClick={handleDrawEdgeClick}
            selectedEdgeId={selectedEdgeId}
          />
        )}
      </div>

      {/* Merge confirm dialog — dragged node = Survivor default (design-brief §Q1) */}
      {mergePair && workspace && (
        <MergeDialog
          workspace={workspace}
          initialWinner={mergePair.dragged}
          initialLoser={mergePair.target}
          canReveal={canReveal}
          onClose={() => setMergePair(null)}
          onMerged={handleMerged}
        />
      )}

      {/* Edge picker dialog — kind-aware, auto-orient (design-brief §Q2) */}
      {edgePicker && workspace && (
        <EdgePickerDialog
          workspace={workspace}
          rawSource={edgePicker.source}
          rawTarget={edgePicker.target}
          onClose={() => setEdgePicker(null)}
          onCreated={handleEdgeCreated}
        />
      )}
    </div>
  );
}
