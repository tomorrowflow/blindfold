// Shared fetch seam for GET /v1/management/review-inbox (viewer-gated, ADR-0035,
// issue #152) — mirrors auditApi.ts's `locked` result shape so ReviewInbox.tsx can
// render the same locked/denied treatment the audit log view uses.

import type { EntityKind } from "./entityListApi";

export type ReviewItem = {
  id: string;
  real: string;
  provisional_surrogate: string;
  context: string;
  context_offset: number;
  kind: EntityKind;
};

export type ReviewInboxFetchResult = { locked: true } | { locked: false; items: ReviewItem[] };

export async function fetchReviewInbox(workspace: string): Promise<ReviewInboxFetchResult> {
  const params = new URLSearchParams({ workspace });
  const resp = await fetch(`/v1/management/review-inbox?${params.toString()}`);
  if (resp.status === 403) {
    return { locked: true };
  }
  const data = await resp.json();
  return { locked: false, items: data.items ?? [] };
}

// Reject scope (ADR-0010 #423 amendment, issue #444): "workspace" (the item's own
// workspace, captured at detection time) or the explicit "all" fail-open. The
// request always carries the choice -- there is no implicit default on the wire.
export type RejectScope = "workspace" | "all";

export type RejectOutcome =
  | { outcome: "ok"; scope: RejectScope }
  | { outcome: "error"; detail: string };

export async function rejectReviewItem(id: string, scope: RejectScope): Promise<RejectOutcome> {
  const r = await fetch(`/v1/management/review-inbox/${encodeURIComponent(id)}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope }),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    return { outcome: "error", detail: body.detail || `HTTP ${r.status}` };
  }
  const data = await r.json();
  return { outcome: "ok", scope: data.scope };
}

// Learned allowlist entries (ADR-0010 #423 amendment decision 4, issue #444): the
// Rejected view's own list/remove/widen seam. Never the seeded half -- it has no
// listing affordance (review.Allowlist.learned_entries).
export type LearnedAllowlistEntry = {
  token: string;
  workspace: string | null;
  all_workspaces: boolean;
};

export async function fetchLearnedAllowlist(workspace: string): Promise<LearnedAllowlistEntry[]> {
  const params = new URLSearchParams({ workspace });
  const r = await fetch(`/v1/management/allowlist/learned?${params.toString()}`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const data = await r.json();
  return data.entries ?? [];
}

function learnedEntryUrl(token: string, workspace: string | null): string {
  const base = `/v1/management/allowlist/learned/${encodeURIComponent(token)}`;
  return workspace === null ? base : `${base}?${new URLSearchParams({ workspace }).toString()}`;
}

export async function removeLearnedAllowlistEntry(
  token: string,
  workspace: string | null
): Promise<void> {
  const r = await fetch(learnedEntryUrl(token, workspace), { method: "DELETE" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
}

// Widen only ever targets a workspace-scoped row -- the Rejected view never shows
// a Widen control on an already-all-workspaces row, so this never takes `null`.
export async function widenLearnedAllowlistEntry(token: string, workspace: string): Promise<void> {
  const params = new URLSearchParams({ workspace });
  const r = await fetch(
    `/v1/management/allowlist/learned/${encodeURIComponent(token)}/widen?${params.toString()}`,
    { method: "POST" }
  );
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
}
