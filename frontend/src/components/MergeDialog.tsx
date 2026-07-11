// MergeDialog (issue #97): the settled winner/loser confirm dialog (ADR-0016),
// opened from a per-row Merge entry + same-kind candidate picker (the final
// design's presentation, adopted per the issue's own instruction). Swap is the
// sole authority on winner/loser — "check order implies nothing"
// (entity-list-view-design-brief §5.5). Each side carries an optional gated
// "Reveal to confirm identity" (audited, ADR-0015) and its retired-surrogate
// chips — the closest safe, decrypt-free analogue to "variations" the current
// backend can offer; a true real-variations reveal needs a new gated endpoint
// (backend gap, not built by this migration slice — see commit notes).

import { useState } from "react";
import type { EntityListRow } from "../lib/entityListApi";
import { mergeEntities } from "../lib/entityListApi";
import { RevealButton } from "./RevealButton";
import { useToast } from "./ToastContext";

type MergeDialogProps = {
  workspace: string;
  initialWinner: EntityListRow;
  initialLoser: EntityListRow;
  canReveal: boolean;
  onClose: () => void;
  onMerged: (loserId: string) => void;
};

function Card({
  label,
  row,
  workspace,
  canReveal,
}: {
  label: "Survivor" | "Retired";
  row: EntityListRow;
  workspace: string;
  canReveal: boolean;
}) {
  return (
    <div
      className={`bf-merge-card bf-merge-card--${label === "Survivor" ? "winner" : "loser"}`}
      data-testid={`merge-card-${label.toLowerCase()}`}
    >
      <h3>{label}</h3>
      <div className="bf-merge-card-surrogate">{row.active_surrogate}</div>
      <div className="bf-merge-card-kind">Kind: {row.kind}</div>
      {row.retired_surrogates.length > 0 && (
        <div className="bf-merge-card-retired">
          {row.retired_surrogates.map((s) => (
            <span key={s} className="bf-merge-card-chip">
              {s}
            </span>
          ))}
        </div>
      )}
      <div className="bf-merge-card-reveal">
        <RevealButton
          workspace={workspace}
          surrogate={row.active_surrogate}
          canReveal={canReveal}
          compact
        />
      </div>
    </div>
  );
}

export function MergeDialog({
  workspace,
  initialWinner,
  initialLoser,
  canReveal,
  onClose,
  onMerged,
}: MergeDialogProps) {
  const { toast } = useToast();
  const [winner, setWinner] = useState(initialWinner);
  const [loser, setLoser] = useState(initialLoser);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function swap() {
    setWinner(loser);
    setLoser(winner);
  }

  async function confirm() {
    setBusy(true);
    setError(null);
    const result = await mergeEntities(workspace, winner.entity_id, loser.entity_id);
    setBusy(false);
    if (result.outcome === "error") {
      setError(result.detail);
      return;
    }
    toast(
      `Merged ${loser.active_surrogate} into ${winner.active_surrogate} — the retired surrogate stays restorable forever, never deleted.`
    );
    onMerged(loser.entity_id);
  }

  return (
    <div
      className="bf-merge-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="bf-merge-dialog-title"
      data-testid="merge-dialog"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="bf-merge-dialog">
        <h2 id="bf-merge-dialog-title">Confirm merge</h2>
        <p className="bf-merge-dialog-copy">
          The <strong>Survivor</strong> absorbs the <strong>Retired</strong> entity. All edges
          and variations re-home to the Survivor. The retired entity's surrogate is retired —
          restorable forever, never deleted.
        </p>
        <div className="bf-merge-candidates">
          <Card label="Survivor" row={winner} workspace={workspace} canReveal={canReveal} />
          <div className="bf-merge-swap-col">
            <button
              type="button"
              className="bf-btn-secondary"
              onClick={swap}
              title="Swap survivor and retired"
              data-testid="merge-swap"
            >
              ⇄ Swap
            </button>
          </div>
          <Card label="Retired" row={loser} workspace={workspace} canReveal={canReveal} />
        </div>
        {error && <div className="bf-merge-error">{error}</div>}
        <div className="bf-merge-dialog-footer">
          <button type="button" className="bf-btn-secondary" onClick={onClose} data-testid="merge-cancel">
            Cancel
          </button>
          <button
            type="button"
            className="bf-btn-primary"
            disabled={busy}
            onClick={confirm}
            data-testid="merge-confirm"
          >
            Confirm merge
          </button>
        </div>
      </div>
    </div>
  );
}
