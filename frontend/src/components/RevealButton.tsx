// RevealButton (issue #97): the single gated, audited per-row/per-side unmask
// control shared by the entity table and the merge dialog's winner/loser cards
// (ADR-0015 / ADR-0017). Light-friction confirm (no reason field) -> audited
// server-side re-identify -> a transient inline `real:` chip, distinct from the
// prototype's toast-only outcome (issue body, entity-list-view-design-brief §5.6).

import { useEffect, useRef, useState } from "react";
import { Lock } from "./icons";
import { useToast } from "./ToastContext";
import { revealSurrogate } from "../lib/entityListApi";

const REVEAL_CHIP_LIFETIME_MS = 12000;

type RevealButtonProps = {
  workspace: string;
  surrogate: string;
  canReveal: boolean;
  compact?: boolean;
};

export function RevealButton({ workspace, surrogate, canReveal, compact }: RevealButtonProps) {
  const { toast } = useToast();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<string | null>(null);
  const clearTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (clearTimer.current) clearTimeout(clearTimer.current);
  }, []);

  if (!canReveal) {
    return (
      <span
        className={`bf-reveal-badge bf-reveal-badge--locked${compact ? " bf-reveal-badge--compact" : ""}`}
        data-testid="reveal-locked"
        title="re-identifier role required"
      >
        <Lock size={12} /> locked
      </span>
    );
  }

  async function confirmReveal() {
    setConfirming(false);
    setBusy(true);
    setError(null);
    const result = await revealSurrogate(workspace, surrogate);
    setBusy(false);
    if (result.outcome === "locked") {
      setError("Access denied — re-identifier role required.");
      return;
    }
    if (result.outcome === "error") {
      setError(result.detail);
      return;
    }
    setRevealed(result.real);
    toast(`Revealed ${surrogate} — logged to the audit trail.`);
    clearTimer.current = setTimeout(() => setRevealed(null), REVEAL_CHIP_LIFETIME_MS);
  }

  if (revealed !== null) {
    return (
      <span className="bf-reveal-value" data-testid="reveal-value">
        real: {revealed}
      </span>
    );
  }

  return (
    <span className="bf-reveal-wrap">
      <button
        type="button"
        className={`bf-reveal-badge${compact ? " bf-reveal-badge--compact" : ""}`}
        disabled={busy}
        onClick={() => setConfirming(true)}
        data-testid="reveal-btn"
        title="This will be logged"
      >
        Reveal
      </button>
      {error && <span className="bf-reveal-error">{error}</span>}
      {confirming && (
        <div className="bf-reveal-confirm" role="dialog" aria-label="Confirm reveal">
          <p>Revealing the real value will be logged as an audit event.</p>
          <div className="bf-reveal-confirm-actions">
            <button
              type="button"
              className="bf-btn-secondary"
              onClick={() => setConfirming(false)}
              data-testid="reveal-cancel"
            >
              Cancel
            </button>
            <button
              type="button"
              className="bf-btn-primary"
              onClick={confirmReveal}
              data-testid="reveal-confirm"
            >
              Reveal
            </button>
          </div>
        </div>
      )}
    </span>
  );
}
