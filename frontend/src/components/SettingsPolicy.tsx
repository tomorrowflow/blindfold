// Settings -> Workspace policy (issue #120, ADR-0009): the fail-closed safety toggle,
// sitting between Preferences and Import (design brief §3.7). Consumes the policy API
// shipped by #118. Admin-gated -- same convention as the Access nav item
// (frontend/src/components/nav.ts's `requiresRole: "admin"`): a non-admin identity
// never sees this section (hidden, not disabled) and the server independently
// 403s the read/write, so a stale client-side role check can't expose the posture.

import { useEffect, useState } from "react";
import { Lock } from "./icons";
import { useWorkspace } from "./WorkspaceContext";
import { fetchWorkspacePolicy, setWorkspacePolicy, type WorkspacePolicyState } from "../lib/policyApi";

export function SettingsPolicy() {
  const { activeWorkspace } = useWorkspace();
  const [policy, setPolicy] = useState<WorkspacePolicyState | null>(null);
  const [locked, setLocked] = useState(false);
  const isAdmin = activeWorkspace?.roles.includes("admin") ?? false;
  const workspace = activeWorkspace?.slug ?? null;

  useEffect(() => {
    if (!workspace || !isAdmin) {
      setPolicy(null);
      return;
    }
    let cancelled = false;
    fetchWorkspacePolicy(workspace).then((result) => {
      if (cancelled) return;
      if ("locked" in result) {
        setLocked(true);
        return;
      }
      setPolicy(result);
    });
    return () => {
      cancelled = true;
    };
  }, [workspace, isAdmin]);

  async function handleToggle() {
    if (!workspace || !policy) return;
    const result = await setWorkspacePolicy(workspace, !policy.deterministicOnly);
    if ("locked" in result) {
      setLocked(true);
      return;
    }
    setPolicy(result);
  }

  if (!workspace || !isAdmin || locked || !policy) return null;

  const failClosed = policy.failClosed;

  return (
    <section className="bf-settings-section" aria-labelledby="bf-policy-heading">
      <h2 id="bf-policy-heading">Workspace policy</h2>
      <div className={`bf-card bf-policy-card${failClosed ? "" : " bf-policy-card--danger"}`}>
        <div
          className={`bf-policy-icon-badge${failClosed ? "" : " bf-policy-icon-badge--danger"}`}
          data-testid="policy-lock-icon"
        >
          <Lock size={20} aria-hidden="true" />
        </div>
        <div className="bf-policy-body">
          <div className="bf-policy-row">
            <div>
              <p className="bf-policy-label">Fail closed on dependency loss</p>
              <p className="bf-settings-field-hint">
                When a dependency is unavailable, block every request rather than let
                one through unprotected. Admin-gated and consequential — turning this
                off means traffic can proceed without re-identification protection.
              </p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={failClosed}
              aria-label="Fail closed on dependency loss"
              className={`bf-policy-toggle${failClosed ? " bf-policy-toggle--on" : ""}`}
              data-testid="policy-fail-closed-toggle"
              onClick={handleToggle}
            >
              <span className="bf-policy-toggle-knob" />
            </button>
          </div>
          {!failClosed && (
            <p className="bf-policy-danger-note" role="alert" data-testid="policy-danger-note">
              Deterministic-only degrade: L1+L2 keep protecting known entities, but
              L3 candidate-span adjudication is skipped, so a novel entity may cross
              egress unblindfolded until it's confirmed in the review inbox.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
