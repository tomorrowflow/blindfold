// GraphInspector (issue #98): side-panel for the selected graph node.
// Shows kind, active surrogate, retired surrogates (chips), edge list with
// direction arrows, a rename field with collision/dependent-warning feedback
// (identical UX to RenameCell), and the per-node Reveal badge (ochre, gated).
//
// Variations count is intentionally absent — there is no gated variations
// endpoint in the current backend (same gap flagged by #97; follow-up needed).

import { useState } from "react";
import type { EntityListRow } from "../lib/entityListApi";
import { renameSurrogate, deleteRelationship } from "../lib/entityListApi";
import { RevealButton } from "./RevealButton";
import { useToast } from "./ToastContext";

type GraphInspectorProps = {
  workspace: string;
  row: EntityListRow; // entity detail fetched from /entities endpoint
  canReveal: boolean;
  onClose: () => void;
  onRenamed: (entityId: string, newSurrogate: string) => void;
  onEdgeDeleted: (edgeId: string) => void;
  onDrawEdgeClick: () => void;
  selectedEdgeId: string | null;
};

export function GraphInspector({
  workspace,
  row,
  canReveal,
  onClose,
  onRenamed,
  onEdgeDeleted,
  onDrawEdgeClick,
  selectedEdgeId,
}: GraphInspectorProps) {
  const { toast } = useToast();
  const [renameValue, setRenameValue] = useState(row.active_surrogate);
  const [renameError, setRenameError] = useState<string | null>(null);
  const [collision, setCollision] = useState(false);
  const [dependents, setDependents] = useState<{ entity_id: string; active_surrogate: string }[] | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [renameBusy, setRenameBusy] = useState(false);
  const [deletingEdgeId, setDeletingEdgeId] = useState<string | null>(null);

  // Reset rename state when a different node is selected
  // (caller will re-mount if row.entity_id changes)

  async function submitRename(ack: boolean) {
    const trimmed = renameValue.trim();
    if (!trimmed || trimmed === row.active_surrogate) return;
    if (dependents && dependents.length > 0 && !ack && !acknowledged) {
      setRenameError("Acknowledge the dependent warning before saving.");
      return;
    }
    setRenameBusy(true);
    setRenameError(null);
    setCollision(false);
    const result = await renameSurrogate(workspace, row.entity_id, trimmed);
    setRenameBusy(false);
    if (result.outcome === "collision") {
      setCollision(true);
      setRenameError(`Collision: ${result.detail}`);
      return;
    }
    if (result.outcome === "error") {
      setRenameError(result.detail);
      return;
    }
    if (result.result.inconsistent_dependents.length > 0 && !ack && !acknowledged) {
      setDependents(result.result.inconsistent_dependents);
      return;
    }
    toast(`Renamed surrogate to ${result.result.active_surrogate}`);
    onRenamed(row.entity_id, result.result.active_surrogate);
    setDependents(null);
    setAcknowledged(false);
    setRenameValue(result.result.active_surrogate);
  }

  async function deleteEdge(edgeId: string) {
    setDeletingEdgeId(edgeId);
    try {
      await deleteRelationship(workspace, edgeId);
      toast("Edge deleted.");
      onEdgeDeleted(edgeId);
    } catch (e) {
      toast(`Failed to delete edge: ${String(e)}`);
    } finally {
      setDeletingEdgeId(null);
    }
  }

  return (
    <aside className="bf-graph-inspector" data-testid="graph-inspector">
      <div className="bf-graph-inspector-header">
        <h2>Inspector</h2>
        <button
          type="button"
          className="bf-btn-secondary"
          onClick={onClose}
          data-testid="inspector-close"
        >
          ✕
        </button>
      </div>

      <div className="bf-graph-inspector-row" data-testid="inspector-kind">
        <span className="bf-graph-inspector-label">Kind:</span>
        <span
          className={`bf-kind-mark bf-kind-mark--${row.kind}`}
          aria-hidden="true"
        />
        <span className="bf-kind-label">{row.kind}</span>
      </div>

      <div className="bf-graph-inspector-row">
        <span className="bf-graph-inspector-label">Surrogate:</span>
        <span className="bf-graph-inspector-surrogate" data-testid="inspector-surrogate">
          {row.active_surrogate}
        </span>
      </div>

      {/* Retired-surrogate chips */}
      {row.retired_surrogates.length > 0 && (
        <div className="bf-graph-inspector-row">
          <span className="bf-graph-inspector-label">Retired:</span>
          <div className="bf-graph-inspector-retired">
            {row.retired_surrogates.map((s) => (
              <span key={s} className="bf-merge-card-chip">
                {s}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Edge list */}
      <div className="bf-graph-inspector-edges" data-testid="inspector-edges">
        <span className="bf-graph-inspector-label">Relationships:</span>
        <ul className="bf-graph-inspector-edge-list">
          {row.edges.map((e) => (
            <li
              key={e.edge_id}
              className={`bf-graph-inspector-edge${selectedEdgeId === e.edge_id ? " bf-graph-inspector-edge--selected" : ""}`}
              data-testid={`inspector-edge-${e.edge_id}`}
            >
              <span className="bf-graph-inspector-edge-text">
                {e.direction === "outbound" ? "→" : "←"} {e.relation} {e.other_surrogate}
              </span>
              <button
                type="button"
                className="bf-graph-inspector-edge-delete"
                disabled={deletingEdgeId === e.edge_id}
                onClick={() => deleteEdge(e.edge_id)}
                data-testid={`inspector-edge-delete-${e.edge_id}`}
              >
                ✕
              </button>
            </li>
          ))}
          {row.edges.length === 0 && (
            <li className="bf-empty" data-testid="inspector-no-edges">
              No edges
            </li>
          )}
        </ul>
      </div>

      <hr className="bf-graph-inspector-divider" />

      {/* Rename surrogate (collision = hard reject, dependent warning = soft) */}
      <div className="bf-graph-inspector-rename" data-testid="inspector-rename">
        <label htmlFor="inspector-rename-input" className="bf-graph-inspector-label">
          Rename surrogate:
        </label>
        <input
          id="inspector-rename-input"
          type="text"
          className={`bf-surrogate-input${collision ? " bf-surrogate-input--error" : ""}`}
          value={renameValue}
          onChange={(e) => {
            setRenameValue(e.target.value);
            setCollision(false);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") submitRename(false);
          }}
          data-testid="inspector-rename-input"
        />
        {renameError && (
          <span className="bf-rename-error" data-testid="inspector-rename-error">
            {renameError}
          </span>
        )}
        {/* Dependent-warning soft banner (ADR-0017 / design-brief §Q3) */}
        {dependents && dependents.length > 0 && (
          <div className="bf-rename-warn" data-testid="inspector-rename-warn">
            <p>
              Some dependent entities may have inconsistent coherent-world surrogates
              after this rename. Fix them individually (no cascade this slice).
            </p>
            <label>
              <input
                type="checkbox"
                checked={acknowledged}
                onChange={(e) => setAcknowledged(e.target.checked)}
                data-testid="inspector-rename-ack-checkbox"
              />
              Acknowledge
            </label>
            <button
              type="button"
              className="bf-btn-primary"
              disabled={!acknowledged || renameBusy}
              onClick={() => submitRename(true)}
              data-testid="inspector-rename-ack-save"
            >
              Acknowledge &amp; rename
            </button>
          </div>
        )}
        <div className="bf-rename-actions">
          <button
            type="button"
            className="bf-rename-save"
            disabled={renameBusy}
            onClick={() => submitRename(false)}
            data-testid="inspector-rename-save"
          >
            Save
          </button>
        </div>
      </div>

      {/* Draw edge / Delete selected edge actions */}
      <div className="bf-graph-inspector-actions">
        <button
          type="button"
          className="bf-btn-secondary"
          onClick={onDrawEdgeClick}
          data-testid="inspector-draw-edge"
        >
          Draw edge
        </button>
      </div>

      {/* Bottom, full-width, ochre reveal action (ADR-0017 / design-brief §Q4, issue #112) */}
      <div className="bf-graph-inspector-reveal" data-testid="inspector-reveal-row">
        <RevealButton
          workspace={workspace}
          surrogate={row.active_surrogate}
          canReveal={canReveal}
          fullWidth
          label="Reveal & log"
        />
      </div>
    </aside>
  );
}
