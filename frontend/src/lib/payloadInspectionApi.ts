// Settings -> Payload inspection arming control (issue #402, ADR-0059 §4). Backs
// onto #398's proxy-side arm/disarm precondition over GET/POST/DELETE
// /v1/management/payload-inspection (admin-gated per workspace, same
// install-global convention the GLiNER detection endpoints use). Mirrors
// policyApi.ts's `{ locked: true }` shape for a 403 rather than throwing --
// admin-only endpoints on this workspace-scoped surface all read the same way.

export type PayloadInspectionStatus = {
  armed: boolean;
  remainingSeconds: number | null;
};

function payloadInspectionUrl(workspace: string): string {
  return `/v1/management/payload-inspection?workspace=${encodeURIComponent(workspace)}`;
}

function toStatus(data: { armed?: boolean; remaining_seconds?: number | null }): PayloadInspectionStatus {
  return {
    armed: Boolean(data.armed),
    remainingSeconds: data.remaining_seconds ?? null,
  };
}

async function call(
  workspace: string,
  method: "GET" | "POST" | "DELETE"
): Promise<PayloadInspectionStatus | { locked: true }> {
  const r = await fetch(payloadInspectionUrl(workspace), { method });
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
  workspace: string
): Promise<PayloadInspectionStatus | { locked: true }> {
  return call(workspace, "POST");
}

export function disarmPayloadInspection(
  workspace: string
): Promise<PayloadInspectionStatus | { locked: true }> {
  return call(workspace, "DELETE");
}
