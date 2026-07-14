// Settings -> Workspace policy management-API seam (issue #120, ADR-0009). Backs the
// fail-closed safety toggle over GET/PUT /v1/management/workspaces/{slug}/policy
// (admin-gated, shipped by #118). Polarity (do not invert): the toggle ON means
// `deterministicOnly=false` (fail-closed default -- block novel candidates when L3 is
// down); OFF means `deterministicOnly=true` (the audited degrade opt-in).

export type WorkspacePolicyState = {
  deterministicOnly: boolean;
  failClosed: boolean;
};

function policyUrl(workspace: string): string {
  return `/v1/management/workspaces/${encodeURIComponent(workspace)}/policy`;
}

function toState(data: { deterministic_only?: boolean; fail_closed?: boolean }): WorkspacePolicyState {
  return {
    deterministicOnly: Boolean(data.deterministic_only),
    failClosed: Boolean(data.fail_closed),
  };
}

export async function fetchWorkspacePolicy(
  workspace: string
): Promise<WorkspacePolicyState | { locked: true }> {
  const r = await fetch(policyUrl(workspace));
  if (r.status === 403) return { locked: true };
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return toState(await r.json());
}

export async function setWorkspacePolicy(
  workspace: string,
  deterministicOnly: boolean
): Promise<WorkspacePolicyState | { locked: true }> {
  const r = await fetch(policyUrl(workspace), {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ deterministic_only: deterministicOnly }),
  });
  if (r.status === 403) return { locked: true };
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return toState(await r.json());
}
