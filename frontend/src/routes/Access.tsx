// Access view (issue #103): the workspace RBAC admin — lists the identities holding
// roles on the active workspace and lets an admin grant/revoke the ADR-0028 roles
// {viewer, curator, re-identifier, admin} over
// /v1/management/workspaces/{slug}/roles. Admin-gated: a non-admin caller sees a
// locked state, never the editor (the sidebar nav item is separately disabled for
// non-admin identities). No blindfolded entity values are involved (CONTEXT.md) —
// identities/roles are not entities — so leak-audit is N/A; this view issues no
// non-loopback requests.

import { useEffect, useState } from "react";
import { Lock, Plus } from "../components/icons";
import { useWorkspace } from "../components/WorkspaceContext";
import {
  CANONICAL_ROLES,
  fetchWorkspaceRoles,
  grantRole,
  revokeRole,
  type RoleAssignment,
} from "../lib/accessApi";

type IdentityRoles = { identity: string; roles: string[] };

function groupByIdentity(assignments: RoleAssignment[]): IdentityRoles[] {
  const byIdentity = new Map<string, string[]>();
  for (const a of assignments) {
    const roles = byIdentity.get(a.identity) ?? [];
    roles.push(a.role);
    byIdentity.set(a.identity, roles);
  }
  return [...byIdentity.entries()].map(([identity, roles]) => ({ identity, roles }));
}

export function Access() {
  const { activeWorkspace } = useWorkspace();
  const [identities, setIdentities] = useState<IdentityRoles[]>([]);
  const [addingIdentity, setAddingIdentity] = useState(false);
  const [newIdentity, setNewIdentity] = useState("");
  const [newRole, setNewRole] = useState<string>(CANONICAL_ROLES[0]);
  const workspace = activeWorkspace?.slug ?? null;
  const isAdmin = activeWorkspace?.roles.includes("admin") ?? false;

  useEffect(() => {
    if (!workspace || !isAdmin) {
      setIdentities([]);
      return;
    }
    let cancelled = false;
    fetchWorkspaceRoles(workspace).then((result) => {
      if (cancelled) return;
      if ("locked" in result) {
        setIdentities([]);
        return;
      }
      setIdentities(groupByIdentity(result));
    });
    return () => {
      cancelled = true;
    };
  }, [workspace, isAdmin]);

  async function refresh() {
    if (!workspace) return;
    const result = await fetchWorkspaceRoles(workspace);
    if ("locked" in result) return;
    setIdentities(groupByIdentity(result));
  }

  async function handleGrant(identity: string, role: string) {
    if (!workspace) return;
    await grantRole(workspace, identity, role);
    await refresh();
  }

  async function handleRevoke(identity: string, role: string) {
    if (!workspace) return;
    await revokeRole(workspace, identity, role);
    await refresh();
  }

  async function handleAddIdentity() {
    const identity = newIdentity.trim();
    if (!identity || !workspace) return;
    await grantRole(workspace, identity, newRole);
    setAddingIdentity(false);
    setNewIdentity("");
    setNewRole(CANONICAL_ROLES[0]);
    await refresh();
  }

  if (!activeWorkspace) {
    return (
      <div className="bf-card">
        <h1>Access</h1>
        <p className="bf-empty">No workspace selected.</p>
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <div className="bf-card">
        <h1>Access</h1>
        <div className="bf-access-locked" data-testid="access-locked">
          <Lock size={20} />
          <span>You need the admin role to manage access for this workspace.</span>
        </div>
      </div>
    );
  }

  return (
    <div className="bf-card">
      <div className="bf-access-header">
        <div>
          <h1>Access</h1>
          <p className="bf-card-subtitle">
            Roles for <code>{workspace}</code>. Admin-gated — grant and revoke below.
          </p>
        </div>
        <button
          type="button"
          className="bf-btn-primary"
          data-testid="add-identity-btn"
          onClick={() => setAddingIdentity((v) => !v)}
        >
          <Plus size={14} /> Add identity
        </button>
      </div>

      {addingIdentity && (
        <div className="bf-access-add-form" data-testid="add-identity-form">
          <input
            type="text"
            placeholder="identity"
            value={newIdentity}
            onChange={(e) => setNewIdentity(e.target.value)}
            data-testid="add-identity-input"
          />
          <select
            value={newRole}
            onChange={(e) => setNewRole(e.target.value)}
            data-testid="add-identity-role-select"
          >
            {CANONICAL_ROLES.map((role) => (
              <option key={role} value={role}>
                {role}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="bf-btn-primary"
            disabled={!newIdentity.trim()}
            onClick={handleAddIdentity}
            data-testid="add-identity-submit"
          >
            Grant
          </button>
          <button
            type="button"
            className="bf-btn-secondary"
            onClick={() => setAddingIdentity(false)}
            data-testid="add-identity-cancel"
          >
            Cancel
          </button>
        </div>
      )}

      <div className="bf-access-table-wrap">
        <table className="bf-access-table">
          <thead>
            <tr>
              <th>Identity</th>
              <th>Roles</th>
              <th>Grant</th>
            </tr>
          </thead>
          <tbody>
            {identities.map((row) => (
              <tr key={row.identity} data-testid={`access-row-${row.identity}`}>
                <td>
                  <div className="bf-access-identity">
                    <span className="bf-access-avatar" aria-hidden="true">
                      {row.identity.slice(0, 2).toUpperCase()}
                    </span>
                    <span className="bf-access-identity-name">{row.identity}</span>
                  </div>
                </td>
                <td>
                  <div className="bf-access-roles">
                    {row.roles.length === 0 && <span className="bf-locked-msg">No roles</span>}
                    {row.roles.map((role) => (
                      <span
                        key={role}
                        className={`bf-access-role-chip${
                          role === "re-identifier" ? " bf-access-role-chip--re-identifier" : ""
                        }`}
                        data-testid={`role-chip-${role}`}
                      >
                        {role}
                        <button
                          type="button"
                          className="bf-access-role-revoke"
                          aria-label={`Revoke ${role} from ${row.identity}`}
                          data-testid={`revoke-btn-${row.identity}-${role}`}
                          onClick={() => handleRevoke(row.identity, role)}
                        >
                          ✕
                        </button>
                      </span>
                    ))}
                  </div>
                </td>
                <td>
                  <div className="bf-access-grant">
                    {CANONICAL_ROLES.filter((role) => !row.roles.includes(role)).map((role) => (
                      <button
                        key={role}
                        type="button"
                        className="bf-access-grant-btn"
                        data-testid={`grant-btn-${row.identity}-${role}`}
                        onClick={() => handleGrant(row.identity, role)}
                      >
                        <Plus size={12} /> {role}
                      </button>
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
