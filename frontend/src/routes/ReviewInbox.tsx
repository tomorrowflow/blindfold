// Review inbox (issue #99): migrated from the legacy embedded Vue page
// (`/ui/review-inbox`, retired) into the unified shell, restyled to the token set.
// Lists provisional candidates the learning loop (ADR-0010) auto-blindfolded and
// queued for human triage: confirm grows the entity graph, reject grows the
// allowlist. Both actions reactively drop the item from the list — protection
// already happened at request time, this view is only the human side of the loop.
//
// Restyled to the comp (issue #113): subtitle + 820px centered column, a rich
// empty state, 13px candidate cards, and a check icon on Confirm. ReviewItem
// (blindfold.review) still carries only id/real/provisional_surrogate/context —
// no entity kind or detection-confidence signal exists anywhere in the pipeline
// (mining.py / engine.py never attach one) — so the comp's dual-encoded kind
// swatch + kind label on each candidate row is NOT rendered here; inventing a
// kind would misrepresent data the pipeline never produced. Attaching a real
// kind signal to ReviewItem is a backend slice, out of this issue's CSS/JSX scope.

import { useEffect, useState } from "react";
import { useReviewInboxPending } from "../components/ReviewInboxContext";
import { Check, CheckCircle2 } from "../components/icons";

const LIST_URL = "/v1/management/review-inbox";
const CONFIRM_URL = (id: string) => `/v1/management/review-inbox/${encodeURIComponent(id)}/confirm`;
const REJECT_URL = (id: string) => `/v1/management/review-inbox/${encodeURIComponent(id)}/reject`;

type ReviewItem = {
  id: string;
  real: string;
  provisional_surrogate: string;
  context: string;
};

export function ReviewInbox() {
  const [items, setItems] = useState<ReviewItem[] | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { refreshPending } = useReviewInboxPending();

  useEffect(() => {
    let cancelled = false;
    fetch(LIST_URL)
      .then((r) => r.json())
      .then((data: { items: ReviewItem[] }) => {
        if (!cancelled) setItems(data.items ?? []);
      })
      .catch(() => {
        if (!cancelled) setItems([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function triage(item: ReviewItem, url: string) {
    setBusyId(item.id);
    setError(null);
    try {
      const r = await fetch(url, { method: "POST" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setItems((prev) => (prev ?? []).filter((i) => i.id !== item.id));
      refreshPending();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="bf-card bf-review-inbox" data-testid="review-inbox-page">
      <h1>Review inbox</h1>
      <p className="bf-card-subtitle">
        Provisional surrogates detected in traffic. Confirm to keep, or reject to
        discard the candidate.
      </p>
      {error && <p className="bf-review-inbox-error">{error}</p>}
      {items === null && <p className="bf-review-inbox-loading">Loading…</p>}
      {items !== null && items.length === 0 && (
        <div className="bf-review-inbox-empty" data-testid="review-inbox-empty">
          <span className="bf-review-inbox-empty-badge" data-testid="review-inbox-empty-badge">
            <CheckCircle2 size={28} aria-hidden="true" />
          </span>
          <h2>Inbox clear</h2>
          <p>Every provisional candidate has been reviewed.</p>
        </div>
      )}
      {items !== null && items.length > 0 && (
        <ul className="bf-review-inbox-list">
          {items.map((item) => (
            <li key={item.id} className="bf-review-inbox-item" data-testid="review-inbox-item">
              <div className="bf-review-inbox-item-header">
                <span className="bf-review-inbox-item-real">{item.real}</span>
                <span className="bf-review-inbox-item-surrogate">
                  → {item.provisional_surrogate}
                </span>
              </div>
              <p className="bf-review-inbox-item-context">{item.context}</p>
              <div className="bf-review-inbox-item-actions">
                <button
                  type="button"
                  className="bf-btn-outline"
                  disabled={busyId === item.id}
                  onClick={() => triage(item, REJECT_URL(item.id))}
                >
                  Reject
                </button>
                <button
                  type="button"
                  className="bf-btn-lime"
                  disabled={busyId === item.id}
                  onClick={() => triage(item, CONFIRM_URL(item.id))}
                >
                  <Check size={16} aria-hidden="true" />
                  Confirm
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
