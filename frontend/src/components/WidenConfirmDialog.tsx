// Widen confirmation (ADR-0010 #423 amendment decision 4, issue #444): widening a
// workspace-scoped learned entry to all-workspaces is a chosen fail-open, exactly
// the same end state as a scope: "all" reject, so it carries the identical
// disclosure and needs confirmation. There is no narrowing action.

import { useState } from "react";
import { widenLearnedAllowlistEntry } from "../lib/reviewInboxApi";
import { ALL_WORKSPACES_DISCLOSURE } from "../lib/allowlistDisclosure";

type WidenConfirmDialogProps = {
  token: string;
  workspace: string;
  onClose: () => void;
  onWidened: () => void;
};

export function WidenConfirmDialog({ token, workspace, onClose, onWidened }: WidenConfirmDialogProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await widenLearnedAllowlistEntry(token, workspace);
      onWidened();
    } catch (e) {
      setBusy(false);
      setError(String(e));
    }
  }

  return (
    <div
      className="bf-widen-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="bf-widen-dialog-title"
      data-testid="widen-dialog"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="bf-widen-dialog">
        <h2 id="bf-widen-dialog-title">Widen to all workspaces</h2>
        <p className="bf-widen-dialog-disclosure" data-testid="widen-dialog-disclosure">
          {ALL_WORKSPACES_DISCLOSURE}
        </p>
        {error && <div className="bf-widen-dialog-error">{error}</div>}
        <div className="bf-widen-dialog-footer">
          <button
            type="button"
            className="bf-btn-secondary"
            onClick={onClose}
            data-testid="widen-dialog-cancel"
          >
            Cancel
          </button>
          <button
            type="button"
            className="bf-btn-outline"
            disabled={busy}
            onClick={confirm}
            data-testid="widen-dialog-confirm"
          >
            Widen
          </button>
        </div>
      </div>
    </div>
  );
}
