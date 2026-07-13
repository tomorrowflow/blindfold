// EdgeChips (issue #97): one chip per outbound relationship (employer /
// subsidiary_of), each with delete and kind-constrained re-target (single select,
// delete+create semantics) — not the prototype's read-only chips
// (entity-list-view-design-brief §5.3/§6). Re-target does not re-derive dependent
// coherent-world surrogates (#25, deferred) — a small inline note says so.

import { useState } from "react";
import type { EdgeSummary, EntityKind, EntityListRow } from "../lib/entityListApi";
import { createRelationship, deleteRelationship } from "../lib/entityListApi";

type EdgeChipsProps = {
  workspace: string;
  row: EntityListRow;
  allRows: EntityListRow[];
  onEdgesChanged: (edges: EdgeSummary[]) => void;
};

export function EdgeChips({ workspace, row, allRows, onEdgesChanged }: EdgeChipsProps) {
  const outbound = row.edges.filter((e) => e.direction === "outbound");

  if (outbound.length === 0) {
    return <span className="bf-edge-chips-empty">—</span>;
  }

  return (
    <div className="bf-edge-chips" data-testid={`edge-chips-${row.entity_id}`}>
      {outbound.map((edge) => (
        <EdgeChip
          key={edge.edge_id}
          workspace={workspace}
          row={row}
          edge={edge}
          allRows={allRows}
          onEdgesChanged={onEdgesChanged}
        />
      ))}
    </div>
  );
}

function EdgeChip({
  workspace,
  row,
  edge,
  allRows,
  onEdgesChanged,
}: {
  workspace: string;
  row: EntityListRow;
  edge: EdgeSummary;
  allRows: EntityListRow[];
  onEdgesChanged: (edges: EdgeSummary[]) => void;
}) {
  const [retargeting, setRetargeting] = useState(false);
  const [pendingTarget, setPendingTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Kind-constrained: both employer and subsidiary_of target only term entities.
  const candidates = allRows.filter(
    (r) => r.kind === ("term" as EntityKind) && r.entity_id !== row.entity_id
  );

  async function handleDelete() {
    setBusy(true);
    setError(null);
    try {
      await deleteRelationship(workspace, edge.edge_id);
      onEdgesChanged(row.edges.filter((e) => e.edge_id !== edge.edge_id));
    } catch (e) {
      setError(String(e));
      setBusy(false);
    }
  }

  async function handleRetarget() {
    if (!pendingTarget || pendingTarget === edge.other_entity_id) {
      setRetargeting(false);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await deleteRelationship(workspace, edge.edge_id);
      const created = await createRelationship(workspace, {
        sourceKind: row.kind,
        sourceId: row.entity_id,
        relation: edge.relation,
        targetKind: "term",
        targetId: pendingTarget,
      });
      const targetRow = allRows.find((r) => r.entity_id === pendingTarget);
      const newEdge: EdgeSummary = {
        edge_id: created.id,
        relation: edge.relation,
        direction: "outbound",
        other_surrogate: targetRow ? targetRow.active_surrogate : pendingTarget,
        other_entity_id: pendingTarget,
        target_kind: "term",
      };
      onEdgesChanged(row.edges.filter((e) => e.edge_id !== edge.edge_id).concat([newEdge]));
      setRetargeting(false);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <span
      className={`bf-edge-chip bf-edge-chip--${edge.target_kind}`}
      data-testid={`edge-chip-${edge.edge_id}`}
    >
      <span className="bf-edge-chip-label">
        <span className="bf-edge-chip-relation" data-testid={`edge-chip-relation-${edge.edge_id}`}>
          {edge.relation}
        </span>
        <span className="bf-edge-chip-target" data-testid={`edge-chip-target-${edge.edge_id}`}>
          {edge.other_surrogate}
        </span>
      </span>
      <button
        type="button"
        className="bf-edge-chip-delete"
        title={`Remove ${edge.relation} edge`}
        disabled={busy}
        onClick={handleDelete}
        data-testid={`edge-chip-delete-${edge.edge_id}`}
      >
        ×
      </button>
      <button
        type="button"
        className="bf-edge-chip-retarget"
        title={`Re-target ${edge.relation} edge`}
        disabled={busy}
        onClick={() => {
          setPendingTarget(edge.other_entity_id);
          setRetargeting((v) => !v);
        }}
        data-testid={`edge-chip-retarget-${edge.edge_id}`}
      >
        ↔
      </button>
      {error && <span className="bf-edge-chip-error">{error}</span>}
      {retargeting && (
        <div className="bf-retarget-picker">
          {candidates.length === 0 ? (
            <span className="bf-retarget-empty">No term entities available</span>
          ) : (
            <select
              value={pendingTarget}
              onChange={(e) => setPendingTarget(e.target.value)}
              data-testid={`edge-chip-retarget-select-${edge.edge_id}`}
            >
              {candidates.map((c) => (
                <option key={c.entity_id} value={c.entity_id}>
                  {c.active_surrogate}
                </option>
              ))}
            </select>
          )}
          <p className="bf-retarget-note">
            Re-target does not re-derive this entity's coherent-world surrogates.
          </p>
          <button
            type="button"
            className="bf-btn-primary"
            disabled={busy || candidates.length === 0}
            onClick={handleRetarget}
            data-testid={`edge-chip-retarget-apply-${edge.edge_id}`}
          >
            Apply
          </button>
          <button
            type="button"
            className="bf-btn-secondary"
            onClick={() => setRetargeting(false)}
          >
            Cancel
          </button>
        </div>
      )}
    </span>
  );
}
