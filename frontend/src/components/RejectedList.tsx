// Rejected view (ADR-0010 #423 amendment decision 4, issue #444): lists the
// learned allowlist entries visible to the current workspace -- that workspace's
// own entries plus every all-workspaces entry, each row's scope shown. Seeded
// entries never appear (the API never lists them). Remove needs no confirmation
// (mirrors EdgeChips' own immediate-delete precedent); Widen does, via
// WidenConfirmDialog, since it's a chosen fail-open.

import { useEffect, useState } from "react";
import {
  fetchLearnedAllowlist,
  removeLearnedAllowlistEntry,
  type LearnedAllowlistEntry,
} from "../lib/reviewInboxApi";
import { WidenConfirmDialog } from "./WidenConfirmDialog";

export function RejectedList({ workspace }: { workspace: string }) {
  const [entries, setEntries] = useState<LearnedAllowlistEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [widenTarget, setWidenTarget] = useState<LearnedAllowlistEntry | null>(null);

  function refresh() {
    fetchLearnedAllowlist(workspace)
      .then(setEntries)
      .catch(() => setEntries([]));
  }

  useEffect(() => {
    setEntries(null);
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace]);

  async function remove(entry: LearnedAllowlistEntry) {
    setError(null);
    try {
      await removeLearnedAllowlistEntry(entry.token, entry.workspace);
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  if (entries === null) {
    return <p className="bf-review-inbox-loading">Loading…</p>;
  }

  return (
    <div data-testid="rejected-view">
      {error && <p className="bf-review-inbox-error">{error}</p>}
      {entries.length === 0 ? (
        <div className="bf-review-inbox-empty" data-testid="rejected-list-empty">
          <h2>No rejected values</h2>
          <p>Values rejected from the review inbox appear here.</p>
        </div>
      ) : (
        <ul className="bf-rejected-list" data-testid="rejected-list">
          {entries.map((entry) => (
            <li
              key={`${entry.token}:${entry.workspace ?? ""}`}
              className="bf-rejected-item"
              data-testid="rejected-item"
            >
              <span className="bf-rejected-item-token">{entry.token}</span>
              <span className="bf-rejected-item-scope" data-testid="rejected-item-scope">
                {entry.all_workspaces ? "All workspaces" : "This workspace"}
              </span>
              <div className="bf-rejected-item-actions">
                <button
                  type="button"
                  className="bf-btn-outline"
                  onClick={() => remove(entry)}
                  data-testid="rejected-item-remove"
                >
                  Remove
                </button>
                {!entry.all_workspaces && (
                  <button
                    type="button"
                    className="bf-btn-outline"
                    onClick={() => setWidenTarget(entry)}
                    data-testid="rejected-item-widen"
                  >
                    Widen to all workspaces
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {widenTarget && (
        <WidenConfirmDialog
          token={widenTarget.token}
          workspace={widenTarget.workspace as string}
          onClose={() => setWidenTarget(null)}
          onWidened={() => {
            setWidenTarget(null);
            refresh();
          }}
        />
      )}
    </div>
  );
}
