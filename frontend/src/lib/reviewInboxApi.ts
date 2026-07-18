// Shared fetch seam for GET /v1/management/review-inbox (viewer-gated, ADR-0035,
// issue #152) — mirrors auditApi.ts's `locked` result shape so ReviewInbox.tsx can
// render the same locked/denied treatment the audit log view uses.

export type ReviewItem = {
  id: string;
  real: string;
  provisional_surrogate: string;
  context: string;
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
