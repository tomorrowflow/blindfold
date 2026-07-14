// EntityListRow (issue #97): one row of the entity-centric table (settled §7
// resolution: entity-centric, not the two-table layout). Kind is immutable (no
// kind-edit control); there is no delete-entity action — the only removal is
// retire-via-merge.

import { useState } from "react";
import type { EdgeSummary, EntityListRow as Row } from "../lib/entityListApi";
import { RenameCell } from "./RenameCell";
import { EdgeChips } from "./EdgeChips";
import { RevealButton } from "./RevealButton";

type EntityListRowProps = {
  workspace: string;
  row: Row;
  allRows: Row[];
  canReveal: boolean;
  highlighted: boolean;
  onRenamed: (entityId: string, newSurrogate: string) => void;
  onEdgesChanged: (entityId: string, edges: EdgeSummary[]) => void;
  onStartMerge: (winner: Row, loser: Row) => void;
};

export function EntityListRow({
  workspace,
  row,
  allRows,
  canReveal,
  highlighted,
  onRenamed,
  onEdgesChanged,
  onStartMerge,
}: EntityListRowProps) {
  const [pickingMerge, setPickingMerge] = useState(false);
  const [candidateId, setCandidateId] = useState("");
  const [renameSignal, setRenameSignal] = useState(0);

  const sameKindCandidates = allRows.filter(
    (r) => r.kind === row.kind && r.entity_id !== row.entity_id
  );

  return (
    <tr
      className={highlighted ? "bf-row-highlighted" : undefined}
      data-testid={`entity-row-${row.entity_id}`}
      data-kind={row.kind}
    >
      <td>
        <span className={`bf-kind-mark bf-kind-mark--${row.kind}`} aria-hidden="true" />
        <span className="bf-kind-label">{row.kind}</span>
      </td>
      <td className="bf-surrogate-cell">
        <RenameCell
          workspace={workspace}
          entityId={row.entity_id}
          surrogate={row.active_surrogate}
          onRenamed={(newSurrogate) => onRenamed(row.entity_id, newSurrogate)}
          editSignal={renameSignal}
        />
      </td>
      <td>
        <EdgeChips
          workspace={workspace}
          row={row}
          allRows={allRows}
          onEdgesChanged={(edges) => onEdgesChanged(row.entity_id, edges)}
        />
      </td>
      <td className="bf-dependents-cell" data-testid={`dependents-count-${row.entity_id}`}>
        {row.dependents}
      </td>
      <td className="bf-actions-cell">
        <button
          type="button"
          className="bf-rename-trigger"
          title="Rename surrogate"
          aria-label="Rename surrogate"
          onClick={() => setRenameSignal((v) => v + 1)}
          data-testid={`rename-trigger-${row.entity_id}`}
        >
          ✎
        </button>
        {sameKindCandidates.length > 0 && (
          <div className="bf-merge-trigger-wrap">
            <button
              type="button"
              className="bf-merge-trigger"
              onClick={() => setPickingMerge((v) => !v)}
              data-testid={`merge-trigger-${row.entity_id}`}
            >
              Merge…
            </button>
            {pickingMerge && (
              <div className="bf-merge-picker" data-testid={`merge-picker-${row.entity_id}`}>
                <select
                  value={candidateId}
                  onChange={(e) => setCandidateId(e.target.value)}
                  data-testid={`merge-picker-select-${row.entity_id}`}
                >
                  <option value="">Select a same-kind entity…</option>
                  {sameKindCandidates.map((c) => (
                    <option key={c.entity_id} value={c.entity_id}>
                      {c.active_surrogate}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="bf-btn-primary"
                  disabled={!candidateId}
                  data-testid={`merge-picker-start-${row.entity_id}`}
                  onClick={() => {
                    const candidate = sameKindCandidates.find((c) => c.entity_id === candidateId);
                    if (!candidate) return;
                    onStartMerge(row, candidate);
                    setPickingMerge(false);
                    setCandidateId("");
                  }}
                >
                  Start merge
                </button>
                <button
                  type="button"
                  className="bf-btn-secondary"
                  onClick={() => {
                    setPickingMerge(false);
                    setCandidateId("");
                  }}
                >
                  Cancel
                </button>
              </div>
            )}
          </div>
        )}
        <RevealButton workspace={workspace} surrogate={row.active_surrogate} canReveal={canReveal} />
      </td>
    </tr>
  );
}
