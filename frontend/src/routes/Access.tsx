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
  ROLE_MEANINGS,
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
  const { activeWorkspace, identity: selfIdentity } = useWorkspace();
  const [identities, setIdentities] = useState<IdentityRoles[]>([]);
  const [addingIdentity, setAddingIdentity] = useState(false);
  const [newIdentity, setNewIdentity] = useState("");
  const [newRole, setNewRole] = useState<string>(CANONICAL_ROLES[0]);
  const [confirmingLockout, setConfirmingLockout] = useState(false);
  // Set the instant the caller confirms revoking their own admin role: the
  // server denies that identity's own next roles-endpoint call (they no longer
  // hold admin), so this view flips to the locked state itself rather than
  // relying on a refetch that would just 403.
  const [selfLostAdmin, setSelfLostAdmin] = useState(false);
  const workspace = activeWorkspace?.slug ?? null;
  const isAdmin = (activeWorkspace?.roles.includes("admin") ?? false) && !selfLostAdmin;
  const adminHolders = identities.filter((row) => row.roles.includes("admin"));
  const isSelfOnlyAdmin =
    adminHolders.length === 1 && adminHolders[0]?.identity === selfIdentity;

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

  async function commitRevoke(identity: string, role: string) {
    if (!workspace) return;
    await revokeRole(workspace, identity, role);
    await refresh();
  }

  function handleRevoke(identity: string, role: string) {
    // Roles are flat (ADR-0028) — an identity holds `admin` or doesn't, so
    // revoking your own admin role is always your only one. Doing so ends this
    // session's own access to this view, and — if no one else holds admin —
    // locks every identity out of workspace administration. Either way, that's
    // consequential enough to confirm before committing.
    if (role === "admin" && identity === selfIdentity) {
      setConfirmingLockout(true);
      return;
    }
    void commitRevoke(identity, role);
  }

  async function confirmLockoutRevoke() {
    if (!workspace) return;
    setConfirmingLockout(false);
    setSelfLostAdmin(true);
    await revokeRole(workspace, selfIdentity, "admin");
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
      <div className="bf-status-view">
        <h1>Access</h1>
        <p className="bf-empty">No workspace selected.</p>
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <div className="bf-status-view">
        <h1>Access</h1>
        <div className="bf-access-locked" data-testid="access-locked">
          <Lock size={20} />
          <span>You need the admin role to manage access for this workspace.</span>
        </div>
      </div>
    );
  }

  return (
    <div className="bf-status-view">
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

      <div className="bf-card bf-access-table-wrap">
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
                          role === "re-identifier" || role === "curator"
                            ? ` bf-access-role-chip--${role}`
                            : ""
                        }`}
                        data-testid={`role-chip-${role}`}
                        title={ROLE_MEANINGS[role as (typeof CANONICAL_ROLES)[number]]}
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
                  {confirmingLockout && row.identity === selfIdentity && (
                    <div
                      className="bf-access-lockout-warning"
                      role="dialog"
                      aria-label="Confirm admin self-revoke"
                      data-testid="admin-lockout-warning"
                    >
                      <p>
                        {isSelfOnlyAdmin ? (
                          <>
                            You are the only admin on <code>{workspace}</code> — revoking your own
                            admin role will lock every identity out of workspace administration.
                          </>
                        ) : (
                          <>
                            Revoking your own admin role will remove your access to manage{" "}
                            <code>{workspace}</code>.
                          </>
                        )}
                      </p>
                      <div className="bf-access-lockout-actions">
                        <button
                          type="button"
                          className="bf-btn-secondary"
                          onClick={() => setConfirmingLockout(false)}
                          data-testid="admin-lockout-cancel"
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          className="bf-btn-danger"
                          onClick={confirmLockoutRevoke}
                          data-testid="admin-lockout-confirm"
                        >
                          Revoke anyway
                        </button>
                      </div>
                    </div>
                  )}
                </td>
                <td>
                  <div className="bf-access-grant">
                    {CANONICAL_ROLES.filter((role) => !row.roles.includes(role)).map((role) => (
                      <button
                        key={role}
                        type="button"
                        className={`bf-access-grant-btn${
                          role === "re-identifier" || role === "curator"
                            ? ` bf-access-grant-btn--${role}`
                            : ""
                        }`}
                        data-testid={`grant-btn-${row.identity}-${role}`}
                        title={ROLE_MEANINGS[role]}
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
