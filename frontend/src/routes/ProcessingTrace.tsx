// Processing trace view (ADR-0035, issue #151): a live, follow-along view of what
// the proxy is doing per request, replacing `tail`ing stdout. GET
// /v1/management/processing-trace, viewer-gated + workspace-scoped the same way the
// audit log is (#16). Every row is a scrubbed exchange-level record -- outcome,
// time, and a detection rollup count only; per-hop detail, the L3 column, reveal
// and deep-links land in follow-up slices.

import { useEffect, useState } from "react";
import { Lock, CheckCircle2, AlertTriangle, CloudOff } from "../components/icons";
import { useWorkspace } from "../components/WorkspaceContext";
import { fetchProcessingTrace, type ProcessingTraceRecord } from "../lib/processingTraceApi";

const POLL_INTERVAL_MS = 2000;
const FRESHNESS_TICK_MS = 1000;

// ADR-0035 decision 7: exactly 3 outcome buckets, zero new color tokens. Upstream
// error is deliberately neutral grey (not red) so an upstream 500 never
// masquerades as a blindfold block.
const OUTCOME_META = {
  passed: { label: "Passed", icon: CheckCircle2, className: "bf-trace-outcome-pill--passed" },
  blocked: { label: "Blocked", icon: AlertTriangle, className: "bf-trace-outcome-pill--blocked" },
  upstream_error: {
    label: "Upstream error",
    icon: CloudOff,
    className: "bf-trace-outcome-pill--upstream-error",
  },
} as const;

function formatTime(ts: string): string {
  const date = new Date(ts);
  return Number.isNaN(date.getTime()) ? ts : date.toLocaleTimeString();
}

export function ProcessingTrace() {
  const { activeWorkspace } = useWorkspace();
  const workspace = activeWorkspace?.slug ?? null;

  const [records, setRecords] = useState<ProcessingTraceRecord[]>([]);
  const [locked, setLocked] = useState(false);
  const [loading, setLoading] = useState(true);
  const [live, setLive] = useState(true);
  const [pollOk, setPollOk] = useState(true);
  const [lastPolledAt, setLastPolledAt] = useState<number | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());

  // Live | Paused pill drives the poll (ADR-0035 decision 9/10): pausing simply
  // stops the interval, it does not clear the already-rendered rows.
  useEffect(() => {
    if (!workspace || !live) return;
    let cancelled = false;
    function poll() {
      fetchProcessingTrace(workspace!)
        .then((result) => {
          if (cancelled) return;
          setPollOk(true);
          setLastPolledAt(Date.now());
          if (result.locked) {
            setLocked(true);
            setRecords([]);
          } else {
            setLocked(false);
            setRecords(result.records);
          }
        })
        .catch(() => {
          if (!cancelled) setPollOk(false);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }
    poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [workspace, live]);

  useEffect(() => {
    const tick = setInterval(() => setNowMs(Date.now()), FRESHNESS_TICK_MS);
    return () => clearInterval(tick);
  }, []);

  if (!workspace) {
    return (
      <div className="bf-card">
        <h1>Processing trace</h1>
        <p className="bf-empty">No workspace selected.</p>
      </div>
    );
  }

  const secondsAgo =
    lastPolledAt === null ? 0 : Math.max(0, Math.round((nowMs - lastPolledAt) / 1000));
  // Newest first -- a live follow-along view reads top-to-bottom without scrolling.
  const rows = [...records].reverse();

  return (
    <div className="bf-card bf-processing-trace" data-testid="processing-trace-page">
      <div className="bf-status-header">
        <div>
          <h1>Processing trace</h1>
          <p className="bf-card-subtitle">
            A live, scrubbed follow-along of what the proxy did per request — never a
            real value, raw hop text, candidate-span text, or a payload diff.
          </p>
        </div>
        <div className="bf-processing-trace-controls">
          <div
            className="bf-search-mode-toggle"
            role="tablist"
            aria-label="Live or paused"
            data-testid="processing-trace-live-toggle"
          >
            <button
              type="button"
              role="tab"
              aria-selected={live}
              className={`bf-search-mode-option${live ? " bf-search-mode-option--active" : ""}`}
              onClick={() => setLive(true)}
              data-testid="processing-trace-live-button"
            >
              Live
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={!live}
              className={`bf-search-mode-option${!live ? " bf-search-mode-option--active" : ""}`}
              onClick={() => setLive(false)}
              data-testid="processing-trace-paused-button"
            >
              Paused
            </button>
          </div>
          <div className="bf-status-freshness" data-testid="processing-trace-freshness">
            {live ? (
              <>
                <span
                  className={`bf-status-freshness-dot ${
                    pollOk ? "bf-status-freshness-dot--ok" : "bf-status-freshness-dot--degraded"
                  }`}
                  aria-hidden="true"
                />
                polled {secondsAgo}s ago
              </>
            ) : (
              <span className="bf-processing-trace-paused-label">Paused</span>
            )}
          </div>
        </div>
      </div>

      {loading && <p className="bf-empty">Loading…</p>}

      {!loading && locked && (
        <div className="bf-audit-log-locked" data-testid="processing-trace-locked">
          <Lock size={20} />
          <span>
            You need the viewer role to see the processing trace for this workspace.
          </span>
        </div>
      )}

      {!loading && !locked && (
        <div className="bf-audit-log-table-wrap">
          <table className="bf-audit-log-table" data-testid="processing-trace-table">
            <thead>
              <tr>
                <th>Outcome</th>
                <th>Time</th>
                <th>Detected</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => {
                const meta = OUTCOME_META[row.outcome];
                const Icon = meta.icon;
                return (
                  <tr key={i} data-testid="processing-trace-row">
                    <td>
                      <span
                        className={`bf-trace-outcome-pill ${meta.className}`}
                        data-outcome={row.outcome}
                        data-testid="processing-trace-row-outcome"
                      >
                        <Icon size={14} />
                        {meta.label}
                      </span>
                    </td>
                    <td className="bf-mono-cell">{formatTime(row.ts)}</td>
                    <td className="bf-mono-cell">{row.detected}</td>
                  </tr>
                );
              })}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={3} className="bf-empty">
                    No processing-trace records yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
