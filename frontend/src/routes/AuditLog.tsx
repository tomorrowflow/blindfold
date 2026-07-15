// Audit log view (issue #102): the full-page counterpart to the top-bar audit
// drawer (issue #95) — GET /v1/management/audit, viewer-gated, scrubbed rows.
// An audit event is a real-space crossing or refusal (CONTEXT.md); structural
// edits (merge/rename/surrogate-edit) are never in this log, same as the drawer.

import { useEffect, useMemo, useState } from "react";
import { Lock, Calendar, UserRound } from "../components/icons";
import { useWorkspace } from "../components/WorkspaceContext";
import { fetchAuditEvents } from "../lib/auditApi";
import { eventKind, KIND_LABELS, type AuditEvent, type CardKind } from "../lib/auditEvents";

type KindFilter = "all" | CardKind;

const TIME_RANGES = {
  "24h": { label: "Last 24 hours", ms: 24 * 60 * 60 * 1000 },
  "7d": { label: "Last 7 days", ms: 7 * 24 * 60 * 60 * 1000 },
  "30d": { label: "Last 30 days", ms: 30 * 24 * 60 * 60 * 1000 },
  all: { label: "All time", ms: null },
} as const;

type TimeRangeKey = keyof typeof TIME_RANGES;

function formatTime(ts: string): string {
  const date = new Date(ts);
  return Number.isNaN(date.getTime()) ? ts : date.toLocaleString();
}

export function AuditLog() {
  const { activeWorkspace } = useWorkspace();
  const workspace = activeWorkspace?.slug ?? null;

  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [actors, setActors] = useState<string[]>([]);
  const [locked, setLocked] = useState(false);
  const [loading, setLoading] = useState(true);

  const [kindFilter, setKindFilter] = useState<KindFilter>("all");
  const [actorFilter, setActorFilter] = useState<string>("all");
  const [timeRange, setTimeRange] = useState<TimeRangeKey>("7d");

  // The actor chip's option list stays stable across the other filters, so it's
  // drawn from one unfiltered fetch, independent of the filtered rows fetch below.
  useEffect(() => {
    if (!workspace) {
      setActors([]);
      return;
    }
    let cancelled = false;
    fetchAuditEvents(workspace).then((result) => {
      if (cancelled || result.locked) return;
      const identities = result.events
        .filter((e) => eventKind(e.event) !== null)
        .map((e) => e.identity)
        .filter((id): id is string => !!id);
      setActors([...new Set(identities)].sort());
    });
    return () => {
      cancelled = true;
    };
  }, [workspace]);

  // Kind, actor and time-range filters are applied server-side (issue #124) —
  // GET /v1/management/audit's kind/actor/since query params, not client-side
  // filtering over a bulk fetch.
  useEffect(() => {
    if (!workspace) {
      setEvents([]);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setLocked(false);
    const rangeMs = TIME_RANGES[timeRange].ms;
    const since = rangeMs === null ? undefined : new Date(Date.now() - rangeMs).toISOString();
    fetchAuditEvents(workspace, {
      kind: kindFilter === "all" ? undefined : kindFilter,
      actor: actorFilter === "all" ? undefined : actorFilter,
      since,
    })
      .then((result) => {
        if (cancelled) return;
        if (result.locked) {
          setLocked(true);
          setEvents([]);
        } else {
          setEvents(result.events);
        }
      })
      .catch(() => {
        if (!cancelled) setEvents([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workspace, kindFilter, actorFilter, timeRange]);

  // A real-space crossing or refusal only — structural edits (merge/rename/
  // surrogate-edit) are never in this log, mirroring the drawer's card set.
  // (The server already excludes these when a kind filter is applied; this
  // guards the unfiltered "All" case, which returns every audit record.)
  const visibleRows = useMemo(
    () =>
      events
        .map((e) => ({ ...e, kind: eventKind(e.event) }))
        .filter((e): e is AuditEvent & { kind: CardKind } => e.kind !== null),
    [events]
  );

  if (!workspace) {
    return (
      <div className="bf-card">
        <h1>Audit log</h1>
        <p className="bf-empty">No workspace selected.</p>
      </div>
    );
  }

  return (
    <div className="bf-card bf-audit-log" data-testid="audit-log-page">
      <h1>Audit log</h1>
      <p className="bf-card-subtitle">
        Every real-space crossing and refusal — reveals, real-name lookups (including
        misses) and blocks. Structural edits are never logged.
      </p>

      {loading && <p className="bf-empty">Loading…</p>}

      {!loading && locked && (
        <div className="bf-audit-log-locked" data-testid="audit-log-locked">
          <Lock size={20} />
          <span>You need the viewer role to see audit events for this workspace.</span>
        </div>
      )}

      {!loading && !locked && (
        <>
          <div className="bf-audit-log-filters">
            <div
              className="bf-search-mode-toggle"
              role="tablist"
              aria-label="Kind filter"
              data-testid="audit-kind-filter"
            >
              {(["all", "reveal", "lookup", "block"] as const).map((kind) => (
                <button
                  key={kind}
                  type="button"
                  role="tab"
                  aria-selected={kindFilter === kind}
                  className={`bf-search-mode-option${kindFilter === kind ? " bf-search-mode-option--active" : ""}`}
                  onClick={() => setKindFilter(kind)}
                  data-testid={`audit-kind-filter-${kind}`}
                >
                  {kind === "all" ? "All" : `${KIND_LABELS[kind]}s`}
                </button>
              ))}
            </div>

            <div className="bf-audit-log-chips">
              <label className="bf-toolbar-field">
                <Calendar size={14} />
                <select
                  value={timeRange}
                  onChange={(e) => setTimeRange(e.target.value as TimeRangeKey)}
                  data-testid="audit-time-filter"
                >
                  {(Object.keys(TIME_RANGES) as TimeRangeKey[]).map((key) => (
                    <option key={key} value={key}>
                      {TIME_RANGES[key].label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="bf-toolbar-field">
                <UserRound size={14} />
                <select
                  value={actorFilter}
                  onChange={(e) => setActorFilter(e.target.value)}
                  data-testid="audit-actor-filter"
                >
                  <option value="all">All actors</option>
                  {actors.map((actor) => (
                    <option key={actor} value={actor}>
                      {actor}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>

          <div className="bf-audit-log-table-wrap">
            <table className="bf-audit-log-table" data-testid="audit-log-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Kind</th>
                  <th>Workspace</th>
                  <th>Actor</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((row, i) => (
                  <tr key={i} data-testid="audit-log-row">
                    <td className="bf-mono-cell">{formatTime(row.ts)}</td>
                    <td>
                      <span
                        className={`bf-audit-kind-pill bf-audit-kind-pill--${row.kind}`}
                        data-kind={row.kind}
                        data-testid="audit-log-row-kind"
                      >
                        {KIND_LABELS[row.kind]}
                      </span>
                    </td>
                    <td className="bf-mono-cell">{row.workspace}</td>
                    <td className="bf-mono-cell">{row.identity}</td>
                    <td>{row.reason}</td>
                  </tr>
                ))}
                {visibleRows.length === 0 && (
                  <tr>
                    <td colSpan={5} className="bf-empty">
                      No audit events match the current filters.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
