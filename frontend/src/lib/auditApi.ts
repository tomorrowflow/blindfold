// Shared fetch seam for GET /v1/management/audit (viewer-gated), consumed by both
// the top-bar AuditDrawer (issue #95) and the full-page Audit log view (issue #102).
// The full-page view's kind/actor/time-range filters are server-side query params
// (issue #124) — see app.py's list_audit_events for the filtering contract.

import type { AuditEvent, CardKind } from "./auditEvents";

export type AuditFetchResult = { locked: true } | { locked: false; events: AuditEvent[] };

export type AuditFilters = {
  kind?: CardKind;
  actor?: string;
  since?: string;
};

export async function fetchAuditEvents(
  workspace: string,
  filters: AuditFilters = {}
): Promise<AuditFetchResult> {
  const params = new URLSearchParams({ workspace });
  if (filters.kind) params.set("kind", filters.kind);
  if (filters.actor) params.set("actor", filters.actor);
  if (filters.since) params.set("since", filters.since);
  const resp = await fetch(`/v1/management/audit?${params.toString()}`);
  if (resp.status === 403) {
    return { locked: true };
  }
  const data = await resp.json();
  return { locked: false, events: data.events ?? [] };
}
