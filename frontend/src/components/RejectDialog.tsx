// Reject scope dialog (ADR-0010 #423 amendment decisions 3/7, issue #444): rejecting
// a review-inbox candidate always shows both scopes -- "This workspace" (preselected)
// and "All workspaces" -- even on an install with one workspace, and the request
// always carries the chosen scope explicitly. The disclosure follows the selection
// (#417: state the effect, not how to feel about it); the "every workspace" wording
// is reserved for "All workspaces". Modelled on MergeDialog's overlay/dialog shape.

import { useState } from "react";
import { rejectReviewItem, type ReviewItem, type RejectScope } from "../lib/reviewInboxApi";
import { scopeDisclosure } from "../lib/allowlistDisclosure";

type RejectDialogProps = {
  item: ReviewItem;
  onClose: () => void;
  onRejected: () => void;
};

export function RejectDialog({ item, onClose, onRejected }: RejectDialogProps) {
  const [scope, setScope] = useState<RejectScope>("workspace");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    setBusy(true);
    setError(null);
    const result = await rejectReviewItem(item.id, scope);
    if (result.outcome === "error") {
      setBusy(false);
      setError(result.detail);
      return;
    }
    onRejected();
  }

  return (
    <div
      className="bf-reject-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="bf-reject-dialog-title"
      data-testid="reject-dialog"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="bf-reject-dialog">
        <h2 id="bf-reject-dialog-title">Reject candidate</h2>
        <div
          className="bf-reject-scope-options"
          role="radiogroup"
          aria-label="Reject scope"
          data-testid="reject-scope-options"
        >
          <label className="bf-reject-scope-option">
            <input
              type="radio"
              name="reject-scope"
              value="workspace"
              checked={scope === "workspace"}
              onChange={() => setScope("workspace")}
              data-testid="reject-scope-workspace"
            />
            This workspace
          </label>
          <label className="bf-reject-scope-option">
            <input
              type="radio"
              name="reject-scope"
              value="all"
              checked={scope === "all"}
              onChange={() => setScope("all")}
              data-testid="reject-scope-all"
            />
            All workspaces
          </label>
        </div>
        <p className="bf-reject-dialog-disclosure" data-testid="reject-dialog-disclosure">
          {scopeDisclosure(scope)}
        </p>
        {error && <div className="bf-reject-dialog-error">{error}</div>}
        <div className="bf-reject-dialog-footer">
          <button
            type="button"
            className="bf-btn-secondary"
            onClick={onClose}
            data-testid="reject-dialog-cancel"
          >
            Cancel
          </button>
          <button
            type="button"
            className="bf-btn-outline"
            disabled={busy}
            onClick={confirm}
            data-testid="reject-dialog-confirm"
          >
            Reject
          </button>
        </div>
      </div>
    </div>
  );
}
