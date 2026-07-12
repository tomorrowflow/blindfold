// EdgePickerDialog (issue #98): picker-on-drop for edge draw gesture (design-brief §Q2).
// Kind-aware: person→term ⇒ only `employer`; term→term ⇒ only `subsidiary_of`;
// other pairs ⇒ reject with an inline error (never shown as a dialog, per spec).
// Reverse-direction term→person drag auto-orients to employer person→term.
// Phrased "Source → Target" using the surrogate labels of the two nodes.

import { useState } from "react";
import type { EntityKind } from "../lib/entityListApi";
import { createRelationship } from "../lib/entityListApi";
import { useToast } from "./ToastContext";

export type EdgePickerNode = {
  id: string;
  kind: EntityKind;
  label: string;
};

type EdgePickerDialogProps = {
  workspace: string;
  rawSource: EdgePickerNode; // as-dragged (before orientation)
  rawTarget: EdgePickerNode;
  onClose: () => void;
  onCreated: (edgeId: string, sourceId: string, targetId: string, relation: "employer" | "subsidiary_of") => void;
};

function determineOrientation(
  sourceKind: EntityKind,
  targetKind: EntityKind
): {
  valid: boolean;
  relation?: "employer" | "subsidiary_of";
  /** true if source/target were swapped for auto-orientation */
  swapped?: boolean;
  error?: string;
} {
  if (sourceKind === "person" && targetKind === "term") {
    return { valid: true, relation: "employer", swapped: false };
  }
  if (sourceKind === "term" && targetKind === "term") {
    return { valid: true, relation: "subsidiary_of", swapped: false };
  }
  if (sourceKind === "term" && targetKind === "person") {
    // Auto-orient: person→term employer (design-brief §Q2 refinement)
    return { valid: true, relation: "employer", swapped: true };
  }
  // person→person or any other pair
  return {
    valid: false,
    error: `Invalid kind pair (${sourceKind}→${targetKind}) — only person→term (employer) or term→term (subsidiary_of) are supported.`,
  };
}

export function EdgePickerDialog({
  workspace,
  rawSource,
  rawTarget,
  onClose,
  onCreated,
}: EdgePickerDialogProps) {
  const { toast } = useToast();
  const [busy, setBusy] = useState(false);

  const orientation = determineOrientation(rawSource.kind, rawTarget.kind);
  const source = orientation.swapped ? rawTarget : rawSource;
  const target = orientation.swapped ? rawSource : rawTarget;

  async function confirm() {
    if (!orientation.valid || !orientation.relation) return;
    setBusy(true);
    try {
      const result = await createRelationship(workspace, {
        sourceKind: source.kind,
        sourceId: source.id,
        relation: orientation.relation,
        targetKind: target.kind,
        targetId: target.id,
      });
      toast(`Edge created: ${source.label} → ${orientation.relation} → ${target.label}`);
      onCreated(result.id, source.id, target.id, orientation.relation);
    } catch (e) {
      toast(`Failed to create edge: ${String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="bf-merge-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Add relationship"
      data-testid="edge-picker-dialog"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="bf-merge-dialog">
        <h2>Add relationship</h2>

        {orientation.valid ? (
          <>
            <p
              className="bf-edge-picker-direction"
              data-testid="edge-picker-direction"
              style={{ fontFamily: "var(--bf-font-mono)", color: "var(--bf-ink)" }}
            >
              {source.label} → {target.label}
            </p>
            <p className="bf-edge-picker-relation" data-testid="edge-picker-relation">
              Relationship: <strong>{orientation.relation}</strong>
            </p>
            {orientation.swapped && (
              <p
                className="bf-edge-picker-note"
                data-testid="edge-picker-auto-orient"
                style={{ color: "var(--bf-ochre)", fontSize: "0.85rem" }}
              >
                Direction auto-oriented: {rawSource.label} → {rawTarget.label} (term→person)
                resolved to person→term employer.
              </p>
            )}
          </>
        ) : (
          <p
            className="bf-edge-picker-error"
            data-testid="edge-picker-error"
            style={{ color: "var(--bf-red)" }}
          >
            {orientation.error}
          </p>
        )}

        <div className="bf-merge-dialog-footer">
          <button
            type="button"
            className="bf-btn-secondary"
            onClick={onClose}
            data-testid="edge-picker-cancel"
          >
            Cancel
          </button>
          {orientation.valid && (
            <button
              type="button"
              className="bf-btn-primary"
              disabled={busy}
              onClick={confirm}
              data-testid="edge-picker-confirm"
            >
              Add edge
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
