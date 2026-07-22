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
  /** Bottom full-width variant (graph inspector, issue #112) vs the default inline badge. */
  fullWidth?: boolean;
  /** Trigger button label — defaults to "Reveal"; the graph inspector uses "Reveal & log". */
  label?: string;
};

export function RevealButton({
  workspace,
  surrogate,
  canReveal,
  compact,
  fullWidth,
  label = "Reveal",
}: RevealButtonProps) {
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
        className={`bf-reveal-badge bf-reveal-badge--locked${compact ? " bf-reveal-badge--compact" : ""}${fullWidth ? " bf-reveal-badge--full" : ""}`}
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
      <span className="bf-reveal-value" data-testid="reveal-value" title={`real: ${revealed}`}>
        real: {revealed}
      </span>
    );
  }

  return (
    <span className={`bf-reveal-wrap${fullWidth ? " bf-reveal-wrap--full" : ""}`}>
      <button
        type="button"
        className={`bf-reveal-badge bf-reveal-badge--ochre${compact ? " bf-reveal-badge--compact" : ""}${fullWidth ? " bf-reveal-badge--full" : ""}`}
        disabled={busy}
        onClick={() => setConfirming(true)}
        data-testid="reveal-btn"
        title="This will be logged"
      >
        <Lock size={12} /> {label}
      </button>
      {error && (
        <span className="bf-reveal-error" title={error}>
          {error}
        </span>
      )}
      {confirming && (
        // Centered modal + backdrop (issue #178) — a portal-free, viewport-collision-proof
        // alternative to the old `position:absolute; top:100%` popover, which clipped off
        // the entity list's right edge and the graph inspector's bottom edge (and overlapped
        // its own trigger there). `position:fixed` centers regardless of the trigger's DOM
        // position, so this stays a plain child of `.bf-reveal-wrap` (row/inspector-scoped
        // locators like `row.getByRole("dialog", ...)` still resolve it).
        <div
          className="bf-reveal-confirm-backdrop"
          onClick={(e) => {
            if (e.target === e.currentTarget) setConfirming(false);
          }}
        >
          <div className="bf-reveal-confirm bf-reveal-confirm--ochre" role="dialog" aria-label="Confirm reveal">
            <span className="bf-reveal-confirm-badge" data-testid="reveal-confirm-badge">
              <Lock size={14} />
            </span>
            <p>Revealing the real value will be recorded as an audit event attributed to you.</p>
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
                className="bf-btn-ochre"
                onClick={confirmReveal}
                data-testid="reveal-confirm"
              >
                Reveal & log
              </button>
            </div>
          </div>
        </div>
      )}
    </span>
  );
}
