// TopBar (issue #95, comp order fidelity issue #114): workspace switcher, spacer,
// audit-drawer trigger, role chips, identity avatar (left to right). Shell-owned
// state flows down; no per-view role toggles.

import { useState, useEffect } from "react";
import { WorkspaceSwitcher } from "./WorkspaceSwitcher";
import { RoleChips } from "./RoleChips";
import { AuditDrawer } from "./AuditDrawer";
import { useWorkspace } from "./WorkspaceContext";

function AuditButton({ onClick, count }: { onClick: () => void; count: number }) {
  return (
    <button
      type="button"
      className="bf-audit-btn"
      onClick={onClick}
      aria-label={`Open audit drawer${count > 0 ? `, ${count} events` : ""}`}
      data-testid="audit-drawer-trigger"
    >
      <span className="bf-audit-btn-label">Audit</span>
      {count > 0 && (
        <span className="bf-audit-btn-badge" aria-hidden="true" data-testid="audit-badge">
          {count}
        </span>
      )}
    </button>
  );
}

// Identity avatar (issue #114): navy circle with the caller's initials at the
// topbar's right end, next to the role chips. Renders nothing until the calling
// identity is known (avoids a misleading blank-initials circle during load).
function IdentityAvatar({ identity }: { identity: string }) {
  if (!identity) return null;
  const initials = identity.slice(0, 2).toUpperCase();
  return (
    <span
      className="bf-identity-avatar"
      aria-label={`Signed in as ${identity}`}
      title={identity}
      data-testid="identity-avatar"
    >
      {initials}
    </span>
  );
}

export function TopBar() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [auditCount, setAuditCount] = useState(0);
  const { activeWorkspace, identity } = useWorkspace();

  // Fetch audit count for the badge whenever the active workspace changes.
  // The drawer fetches its own events on open; this lightweight probe keeps the badge in sync.
  // Viewer-gated: 403 → 0 (no error shown in the badge; drawer shows locked state).
  useEffect(() => {
    if (!activeWorkspace) {
      setAuditCount(0);
      return;
    }
    let cancelled = false;
    fetch(`/v1/management/audit?workspace=${encodeURIComponent(activeWorkspace.slug)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!cancelled) setAuditCount(data?.events?.length ?? 0);
      })
      .catch(() => {
        if (!cancelled) setAuditCount(0);
      });
    return () => {
      cancelled = true;
    };
  }, [activeWorkspace]);

  return (
    <>
      <header className="bf-topbar">
        <WorkspaceSwitcher />
        <div className="bf-topbar-spacer" />
        <AuditButton onClick={() => setDrawerOpen(true)} count={auditCount} />
        <RoleChips />
        <IdentityAvatar identity={identity} />
      </header>
      <AuditDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} />
    </>
  );
}
