// Review inbox (issue #99): migrated from the legacy embedded Vue page
// (`/ui/review-inbox`, retired) into the unified shell, restyled to the token set.
// Lists provisional candidates the learning loop (ADR-0010) auto-blindfolded and
// queued for human triage: confirm grows the entity graph, reject grows the
// allowlist. Both actions reactively drop the item from the list — protection
// already happened at request time, this view is only the human side of the loop.
//
// Restyled to the comp (issue #113): subtitle + 820px centered column, a rich
// empty state, 13px candidate cards, and a check icon on Confirm.
//
// Kind shape + right-aligned inline actions (issue #176): the list seam now
// derives `kind` from ReviewItem.entity_type (issue #171 attached it to the
// item; the endpoint maps it via the same person/term rule confirm's
// _entity_kind_for uses), so the card can render a real dual-encoded kind
// shape (hard rule §6.2: shape + colour, never colour-only) — reuses the
// entity list's own `.bf-kind-mark`/`.bf-kind-mark--{kind}` classes, no new
// token/hex. The row is horizontal: kind shape · content (kind label +
// mono surrogate + highlighted context) · Reject/Confirm right-aligned inline.
//
// Candidate-span highlight (ADR-0035 decision 11, issue #155): context_offset
// is backend-derived from the candidate span's own position, so the context
// window is sliced (not searched) into before/span/after. The highlight tint
// is neutral (--bf-border / --bf-border-soft) — not --bf-ochre-* (reserved for
// audited reveal), not a kind color, not red (not a block), not curator-green
// (would pre-suggest Confirm).

import { useEffect, useState } from "react";
import { useReviewInboxPending } from "../components/ReviewInboxContext";
import { useWorkspace } from "../components/WorkspaceContext";
import { Check, CheckCircle2, Lock } from "../components/icons";
import { fetchReviewInbox, type ReviewItem } from "../lib/reviewInboxApi";

const CONFIRM_URL = (id: string) => `/v1/management/review-inbox/${encodeURIComponent(id)}/confirm`;
const REJECT_URL = (id: string) => `/v1/management/review-inbox/${encodeURIComponent(id)}/reject`;

function ContextWithHighlight({ item }: { item: ReviewItem }) {
  const offset = item.context_offset;
  const end = offset + item.real.length;
  return (
    <p className="bf-review-inbox-item-context" data-testid="review-inbox-item-context">
      {item.context.slice(0, offset)}
      <mark className="bf-review-inbox-item-highlight" data-testid="review-inbox-item-highlight">
        {item.context.slice(offset, end)}
      </mark>
      {item.context.slice(end)}
    </p>
  );
}

export function ReviewInbox() {
  const { activeWorkspace } = useWorkspace();
  const workspace = activeWorkspace?.slug ?? null;

  const [items, setItems] = useState<ReviewItem[] | null>(null);
  const [locked, setLocked] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { refreshPending } = useReviewInboxPending();

  useEffect(() => {
    if (!workspace) {
      setItems([]);
      return;
    }
    let cancelled = false;
    setLocked(false);
    fetchReviewInbox(workspace)
      .then((result) => {
        if (cancelled) return;
        if (result.locked) {
          setLocked(true);
          setItems([]);
        } else {
          setItems(result.items);
        }
      })
      .catch(() => {
        if (!cancelled) setItems([]);
      });
    return () => {
      cancelled = true;
    };
  }, [workspace]);

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
    <div className="bf-review-inbox" data-testid="review-inbox-page">
      <h1>Review inbox</h1>
      <p className="bf-card-subtitle">
        Provisional surrogates detected in traffic. Confirm to keep, or reject to
        discard the candidate.
      </p>
      {error && <p className="bf-review-inbox-error">{error}</p>}
      {items === null && !locked && <p className="bf-review-inbox-loading">Loading…</p>}
      {locked && (
        <div className="bf-review-inbox-locked" data-testid="review-inbox-locked">
          <Lock size={20} />
          <span>You need the viewer role to see the review inbox for this workspace.</span>
        </div>
      )}
      {items !== null && !locked && items.length === 0 && (
        <div className="bf-review-inbox-empty" data-testid="review-inbox-empty">
          <span className="bf-review-inbox-empty-badge" data-testid="review-inbox-empty-badge">
            <CheckCircle2 size={28} aria-hidden="true" />
          </span>
          <h2>Inbox clear</h2>
          <p>Every provisional candidate has been reviewed.</p>
        </div>
      )}
      {items !== null && !locked && items.length > 0 && (
        <ul className="bf-review-inbox-list">
          {items.map((item) => (
            <li key={item.id} className="bf-review-inbox-item" data-testid="review-inbox-item">
              <span
                className={`bf-kind-mark bf-kind-mark--${item.kind}`}
                aria-hidden="true"
              />
              <div className="bf-review-inbox-item-content">
                <div className="bf-review-inbox-item-header">
                  <span className="bf-review-inbox-item-surrogate">
                    {item.provisional_surrogate}
                  </span>
                  <span className="bf-kind-label">{item.kind}</span>
                </div>
                <ContextWithHighlight item={item} />
              </div>
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
