// AuditDrawer (issue #95): 392px right slide-over, ochre top border, triggered by
// a count-badged button in the TopBar. Backed by GET /v1/management/audit (viewer-gated).
// Without viewer role the drawer shows a locked/empty state (no error, no crash).
// Event card kinds: Reveal (ochre), Lookup (ochre), Block (red).

import { useEffect, useState } from "react";
import { X, Lock } from "./icons";
import { useWorkspace } from "./WorkspaceContext";
import { Link } from "react-router-dom";
import { eventKind, KIND_LABELS, type AuditEvent, type CardKind } from "../lib/auditEvents";
import { fetchAuditEvents } from "../lib/auditApi";

type AuditDrawerProps = {
  open: boolean;
  onClose: () => void;
};

export function AuditDrawer({ open, onClose }: AuditDrawerProps) {
  const { activeWorkspace } = useWorkspace();
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [locked, setLocked] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (!activeWorkspace) {
      // No workspace accessible to this identity — treat as locked (no viewer role).
      setLocked(true);
      setLoading(false);
      return;
    }
    setLoading(true);
    setLocked(false);
    fetchAuditEvents(activeWorkspace.slug)
      .then((result) => {
        if (result.locked) {
          setLocked(true);
          setEvents([]);
        } else {
          setEvents(result.events);
        }
      })
      .catch(() => {
        setEvents([]);
      })
      .finally(() => setLoading(false));
  }, [open, activeWorkspace]);

  const cards = events
    .map((e) => ({ ...e, kind: eventKind(e.event) }))
    .filter((e): e is AuditEvent & { kind: CardKind } => e.kind !== null);

  return (
    <div
      className={`bf-audit-drawer${open ? " bf-audit-drawer--open" : ""}`}
      aria-label="Audit drawer"
      data-testid="audit-drawer"
      role="dialog"
      aria-modal="true"
      aria-hidden={!open}
      // inert removes focus and pointer events from a hidden drawer, preventing
      // the footer link from being picked up by Playwright's strict-mode role queries
      // when the drawer is off-screen (issue #95).
      {...(!open ? { inert: "" } : {})}
    >
      <div className="bf-audit-drawer-header">
        <h2 className="bf-audit-drawer-title">Audit · recent real-space events</h2>
        <button
          type="button"
          className="bf-audit-drawer-close"
          aria-label="Close audit drawer"
          onClick={onClose}
        >
          <X size={18} />
        </button>
      </div>
      <p className="bf-audit-drawer-banner">
        Reveals, real-name lookups and blocks. Structural edits like merges and renames
        are never logged.
      </p>

      {loading && <div className="bf-audit-drawer-loading">Loading…</div>}

      {!loading && locked && (
        <div className="bf-audit-drawer-locked" data-testid="audit-drawer-locked">
          <Lock size={20} />
          <span>You need the viewer role to see audit events for this workspace.</span>
        </div>
      )}

      {!loading && !locked && (
        <ul className="bf-audit-event-list">
          {cards.map((e, i) => (
            <li
              key={i}
              className={`bf-audit-event-card bf-audit-event-card--${e.kind}`}
              data-kind={e.kind}
            >
              <span className="bf-audit-event-kind">{KIND_LABELS[e.kind]}</span>
              <span className="bf-audit-event-identity">{e.identity}</span>
              {e.reason && <span className="bf-audit-event-reason">{e.reason}</span>}
            </li>
          ))}
          {cards.length === 0 && (
            <li className="bf-audit-event-empty">No recent events</li>
          )}
        </ul>
      )}

      <div className="bf-audit-drawer-footer">
        <Link to="/audit" className="bf-audit-drawer-footer-link" onClick={onClose}
          aria-label="Open the full audit log view">
          View full audit log
        </Link>
      </div>
    </div>
  );
}
