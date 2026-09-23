// Settings -> Payload inspection arming control (issue #402, ADR-0059 §4;
// selectable retention window, ADR-0059 amendment #431 §4, issue #433). Backs
// onto the proxy-side arm/disarm endpoints over GET/POST/DELETE
// /v1/management/payload-inspection (admin-gated per workspace, same
// install-global convention the GLiNER detection endpoints use). Mirrors
// policyApi.ts's `{ locked: true }` shape for a 403 rather than throwing --
// admin-only endpoints on this workspace-scoped surface all read the same way.

// Wire values for POST's `window` query param -- mirrors
// `payload_inspection.RETENTION_WINDOWS`'s keys exactly.
export type RetentionWindow = "30m" | "2h" | "until_disarmed";

export const RETENTION_WINDOWS: { value: RetentionWindow; label: string; countBound: number }[] = [
  { value: "30m", label: "30 minutes", countBound: 25 },
  { value: "2h", label: "2 hours", countBound: 100 },
  { value: "until_disarmed", label: "Until disarmed", countBound: 200 },
];

export type PayloadInspectionStatus = {
  armed: boolean;
  remainingSeconds: number | null;
  window: RetentionWindow | null;
  countBound: number | null;
  retainedCount: number;
};

function payloadInspectionUrl(workspace: string, window?: RetentionWindow): string {
  const params = new URLSearchParams({ workspace });
  if (window) params.set("window", window);
  return `/v1/management/payload-inspection?${params.toString()}`;
}

function toStatus(data: {
  armed?: boolean;
  remaining_seconds?: number | null;
  window?: RetentionWindow | null;
  count_bound?: number | null;
  retained_count?: number;
}): PayloadInspectionStatus {
  return {
    armed: Boolean(data.armed),
    remainingSeconds: data.remaining_seconds ?? null,
    window: data.window ?? null,
    countBound: data.count_bound ?? null,
    retainedCount: data.retained_count ?? 0,
  };
}

async function call(
  workspace: string,
  method: "GET" | "POST" | "DELETE",
  window?: RetentionWindow
): Promise<PayloadInspectionStatus | { locked: true }> {
  const r = await fetch(payloadInspectionUrl(workspace, window), { method });
  if (r.status === 403) return { locked: true };
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return toStatus(await r.json());
}

export function fetchPayloadInspectionStatus(
  workspace: string
): Promise<PayloadInspectionStatus | { locked: true }> {
  return call(workspace, "GET");
}

export function armPayloadInspection(
  workspace: string,
  window: RetentionWindow
): Promise<PayloadInspectionStatus | { locked: true }> {
  return call(workspace, "POST", window);
}

export function disarmPayloadInspection(
  workspace: string
): Promise<PayloadInspectionStatus | { locked: true }> {
  return call(workspace, "DELETE");
}
