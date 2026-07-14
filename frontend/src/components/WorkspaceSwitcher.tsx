// WorkspaceSwitcher (issue #95, two-line fidelity issue #114): navy icon tile +
// workspace name + mono slug (two lines) + chevron. 288px dropdown menu titled
// "Workspaces you can access". Lists ONLY workspaces the calling identity holds
// ≥1 role on (the server enforces this; the SPA renders what the API returns).
// Switching re-scopes every management API call via the WorkspaceContext.

import { useRef, useState, useEffect } from "react";
import { Layers, ChevronDown, Check } from "./icons";
import { useWorkspace } from "./WorkspaceContext";

export function WorkspaceSwitcher() {
  const { workspaces, activeWorkspace, setActiveWorkspace, loading } = useWorkspace();
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    if (!open) return;
    function handler(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  if (loading) {
    return (
      <div className="bf-workspace-switcher bf-workspace-switcher--loading" aria-busy="true">
        <span className="bf-workspace-icon"><Layers size={18} /></span>
        <span className="bf-workspace-name">Loading…</span>
      </div>
    );
  }

  if (!activeWorkspace) {
    return (
      <div className="bf-workspace-switcher bf-workspace-switcher--empty">
        <span className="bf-workspace-icon"><Layers size={18} /></span>
        <span className="bf-workspace-name">No workspace</span>
      </div>
    );
  }

  return (
    <div className="bf-workspace-switcher-wrap" ref={menuRef}>
      <button
        type="button"
        className="bf-workspace-switcher"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Active workspace: ${activeWorkspace.slug}`}
        onClick={() => setOpen((v) => !v)}
        data-testid="workspace-switcher-trigger"
      >
        <span className="bf-workspace-icon"><Layers size={18} /></span>
        <span className="bf-workspace-switcher-text">
          <span className="bf-workspace-name">{activeWorkspace.name}</span>
          <span className="bf-workspace-slug">{activeWorkspace.slug}</span>
        </span>
        <ChevronDown size={14} className="bf-workspace-chevron" />
      </button>

      {open && (
        <div
          className="bf-workspace-menu"
          role="listbox"
          aria-label="Workspaces you can access"
          data-testid="workspace-menu"
        >
          <div className="bf-workspace-menu-title">Workspaces you can access</div>
          <ul className="bf-workspace-menu-list">
            {workspaces.map((ws) => {
              const isCurrent = ws.slug === activeWorkspace.slug;
              return (
                <li
                  key={ws.slug}
                  role="option"
                  aria-selected={isCurrent}
                  className={`bf-workspace-menu-item${isCurrent ? " bf-workspace-menu-item--current" : ""}`}
                  onClick={() => {
                    setActiveWorkspace(ws);
                    setOpen(false);
                  }}
                >
                  <span className="bf-workspace-menu-item-name">{ws.name}</span>
                  <span className="bf-workspace-menu-item-slug">{ws.slug}</span>
                  {isCurrent && <Check size={14} className="bf-workspace-check" />}
                </li>
              );
            })}
          </ul>
          <div className="bf-workspace-menu-footer">
            Only workspaces you hold a role on appear here
          </div>
        </div>
      )}
    </div>
  );
}
