// Settings -> Detection management-API seam (issue #147, ADR-0034 §5). Backs the
// GLiNER provisioning status + retry section over GET/POST
// /v1/management/detection/gliner[/retry] (admin-gated, same convention as
// policyApi.ts). Install-global, not per-workspace (ADR-0034 §5) -- `workspace`
// only names which workspace's `admin` role gates the call, not a data scope.

export type GlinerDetectionStatusValue =
  | "not_provisioned"
  | "provisioned"
  | "active"
  | "verification_failed";

export type GlinerDetectionStatus = {
  status: GlinerDetectionStatusValue;
  modelPath: string;
  activated: boolean;
  restartRequired: boolean;
  error: string | null;
};

function detectionUrl(workspace: string): string {
  return `/v1/management/detection/gliner?workspace=${encodeURIComponent(workspace)}`;
}

function retryUrl(workspace: string): string {
  return `/v1/management/detection/gliner/retry?workspace=${encodeURIComponent(workspace)}`;
}

function toStatus(data: {
  status: GlinerDetectionStatusValue;
  model_path: string;
  activated?: boolean;
  restart_required?: boolean;
  error?: string | null;
}): GlinerDetectionStatus {
  return {
    status: data.status,
    modelPath: data.model_path,
    activated: Boolean(data.activated),
    restartRequired: Boolean(data.restart_required),
    error: data.error ?? null,
  };
}

export async function fetchGlinerDetectionStatus(
  workspace: string
): Promise<GlinerDetectionStatus | { locked: true }> {
  const r = await fetch(detectionUrl(workspace));
  if (r.status === 403) return { locked: true };
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return toStatus(await r.json());
}

export async function retryGlinerProvisioning(
  workspace: string
): Promise<GlinerDetectionStatus | { locked: true }> {
  const r = await fetch(retryUrl(workspace), { method: "POST" });
  if (r.status === 403) return { locked: true };
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return toStatus(await r.json());
}
