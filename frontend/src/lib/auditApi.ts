// Shared fetch seam for GET /v1/management/audit (viewer-gated), consumed by both
// the top-bar AuditDrawer (issue #95) and the full-page Audit log view (issue #102).

import type { AuditEvent } from "./auditEvents";

export type AuditFetchResult = { locked: true } | { locked: false; events: AuditEvent[] };

export async function fetchAuditEvents(workspace: string): Promise<AuditFetchResult> {
  const resp = await fetch(`/v1/management/audit?workspace=${encodeURIComponent(workspace)}`);
  if (resp.status === 403) {
    return { locked: true };
  }
  const data = await resp.json();
  return { locked: false, events: data.events ?? [] };
}
