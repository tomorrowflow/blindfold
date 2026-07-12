// Access-view management-API seam (issue #103, ADR-0028). Backs the workspace RBAC
// admin: list/grant/revoke the canonical four-role set over
// /v1/management/workspaces/{slug}/roles (admin-gated server-side, already shipped
// and covered by tests/test_audit_viewer_rbac.py). No blindfolded entity values are
// involved — identities and roles are not entities (CONTEXT.md), so this seam carries
// no leak-audit surface of its own.

export const CANONICAL_ROLES = ["viewer", "curator", "re-identifier", "admin"] as const;

export type Role = (typeof CANONICAL_ROLES)[number];

export type RoleAssignment = {
  identity: string;
  workspace: string;
  role: string;
};

function rolesBase(workspace: string): string {
  return `/v1/management/workspaces/${encodeURIComponent(workspace)}/roles`;
}

export async function fetchWorkspaceRoles(
  workspace: string
): Promise<RoleAssignment[] | { locked: true }> {
  const r = await fetch(rolesBase(workspace));
  if (r.status === 403) return { locked: true };
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const data = await r.json();
  return data.assignments ?? [];
}

export async function grantRole(
  workspace: string,
  identity: string,
  role: string
): Promise<void> {
  const r = await fetch(rolesBase(workspace), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ identity, role }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
}

export async function revokeRole(
  workspace: string,
  identity: string,
  role: string
): Promise<void> {
  const r = await fetch(
    `${rolesBase(workspace)}/${encodeURIComponent(identity)}?role=${encodeURIComponent(role)}`,
    { method: "DELETE" }
  );
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
}
