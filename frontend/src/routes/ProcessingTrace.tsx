// Processing trace view (ADR-0035, issue #151): a live, follow-along view of what
// the proxy is doing per request, replacing `tail`ing stdout. GET
// /v1/management/processing-trace, viewer-gated + workspace-scoped the same way the
// audit log is (#16). Every row is a scrubbed exchange-level record -- outcome,
// time, a detection rollup count, and (issue #153) an L3 provider/timing column
// plus a Hops column that expands inline into one card per hop; reveal and
// deep-links remain out of scope for this slice.

import { Fragment, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Lock, CheckCircle2, AlertTriangle, CloudOff, ChevronDown } from "../components/icons";
import { RevealButton } from "../components/RevealButton";
import { useWorkspace } from "../components/WorkspaceContext";
import {
  fetchProcessingTrace,
  type ProcessingTraceHop,
  type ProcessingTraceRecord,
  type ProcessingTraceSurrogate,
} from "../lib/processingTraceApi";

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

function formatMs(ms: number): string {
  // Issue #158: exchanges now run into the tens of seconds (GLiNER-cascade L3
  // minting over a large candidate-span count) -- render >=1000ms as seconds so
  // the collapsed row stays readable.
  if (ms >= 1000) {
    return `${(ms / 1000).toFixed(1)}s`;
  }
  return `${Math.round(ms)}ms`;
}

// Issue #158: `duration_ms` is the whole mint -> forward -> restore -> gate
// round-trip; `upstream_duration_ms` is the sub-span actually spent waiting on
// the upstream provider (Claude) -- `null` when the exchange never reached
// upstream (blocked pre-forward), in which case the full total is blindfold-side.
// The split is derived here, never stored, so it can never drift from the two
// fields the record actually carries.
function formatBlindfoldVsUpstream(durationMs: number, upstreamDurationMs: number | null): string {
  if (upstreamDurationMs === null) {
    return `blindfold ${formatMs(durationMs)}`;
  }
  const blindfoldMs = Math.max(0, durationMs - upstreamDurationMs);
  return `blindfold ${formatMs(blindfoldMs)} / upstream ${formatMs(upstreamDurationMs)}`;
}

function formatL1Counts(counts: Record<string, number>): string {
  const entries = Object.entries(counts);
  if (entries.length === 0) return "0";
  return entries.map(([kind, count]) => `${kind} ${count}`).join(", ");
}

// One hop-injected surrogate's chip (issue #154, ADR-0035): degrades with the
// surrogate's own reveal lifecycle -- "confirmed" reuses the existing audited
// Reveal control (Re-identify path the audit log/entity list already use, not a
// new endpoint); "pending" is still a provisional candidate awaiting triage, so
// it renders a deep-link into the Review inbox instead (never the inbox's own
// real `context`); "rejected" (recognized by neither store) gets no affordance
// at all, just the bare token.
function HopSurrogateChip({
  surrogate,
  workspace,
  canReveal,
}: {
  surrogate: ProcessingTraceSurrogate;
  workspace: string;
  canReveal: boolean;
}) {
  if (surrogate.lifecycle === "confirmed") {
    return (
      <span className="bf-merge-card-chip bf-trace-hop-surrogate-chip">
        {surrogate.token}
        <RevealButton workspace={workspace} surrogate={surrogate.token} canReveal={canReveal} compact />
      </span>
    );
  }
  if (surrogate.lifecycle === "pending") {
    return (
      <span className="bf-merge-card-chip bf-trace-hop-surrogate-chip">
        {surrogate.token}
        <Link
          to="/inbox"
          className="bf-trace-pending-review-link"
          data-testid="processing-trace-pending-review-link"
        >
          Pending review →
        </Link>
      </span>
    );
  }
  return (
    <span className="bf-merge-card-chip" data-testid="processing-trace-rejected-surrogate-chip">
      {surrogate.token}
    </span>
  );
}

// One hop's scrubbed detail card (ADR-0035 per-hop expansion, issue #153): L1/L2/L3
// + suppression counts, L1/L2 timings, and this hop's own injected-surrogate chips,
// each degrading by reveal lifecycle (issue #154). Never a real value,
// candidate-span text, or raw hop text -- the API only ever sends scrubbed fields.
function HopCard({
  hop,
  workspace,
  canReveal,
}: {
  hop: ProcessingTraceHop;
  workspace: string;
  canReveal: boolean;
}) {
  return (
    <div className="bf-trace-hop-card" data-testid="processing-trace-hop-card">
      <div className="bf-trace-hop-card-header">
        <span className="bf-trace-hop-card-index">Hop {hop.hop_index + 1}</span>
        <span className="bf-trace-hop-card-kind">{hop.hop_kind}</span>
      </div>
      <dl className="bf-trace-hop-card-stats">
        <div>
          <dt>L1</dt>
          <dd className="bf-mono-cell">
            {formatL1Counts(hop.l1_counts)} ({formatMs(hop.l1_duration_ms)})
          </dd>
        </div>
        <div>
          <dt>L2</dt>
          <dd className="bf-mono-cell">
            {hop.l2_count} ({formatMs(hop.l2_duration_ms)})
          </dd>
        </div>
        <div>
          <dt>L3</dt>
          <dd className="bf-mono-cell">
            {hop.l3_confirmed} confirmed, {hop.l3_dismissed} dismissed, {hop.l3_suppressed}{" "}
            suppressed
          </dd>
        </div>
      </dl>
      {hop.surrogates.length > 0 && (
        <div className="bf-trace-hop-card-surrogates">
          {hop.surrogates.map((surrogate, i) => (
            <HopSurrogateChip
              key={i}
              surrogate={surrogate}
              workspace={workspace}
              canReveal={canReveal}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function ProcessingTrace() {
  const { activeWorkspace } = useWorkspace();
  const workspace = activeWorkspace?.slug ?? null;
  // Same role check EntityList/GraphEditor already use to gate RevealButton
  // (ADR-0015): re-identifier on the active workspace, not viewer.
  const canReveal = activeWorkspace?.roles.includes("re-identifier") ?? false;

  const [records, setRecords] = useState<ProcessingTraceRecord[]>([]);
  const [locked, setLocked] = useState(false);
  const [loading, setLoading] = useState(true);
  const [live, setLive] = useState(true);
  const [pollOk, setPollOk] = useState(true);
  const [lastPolledAt, setLastPolledAt] = useState<number | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  // Which rows are expanded (issue #153) -- keyed by row index within the
  // newest-first `rows` array, so expansion state survives a poll tick as long as
  // the row's position doesn't shift.
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set());

  function toggleRow(index: number) {
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  }

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
                <th>Total</th>
                <th>Blindfold / Upstream</th>
                <th>Detected</th>
                <th>L3</th>
                <th>Hops</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => {
                const meta = OUTCOME_META[row.outcome];
                const Icon = meta.icon;
                const expanded = expandedRows.has(i);
                const hopCount = row.hops.length;
                return (
                  <Fragment key={i}>
                    <tr
                      className="bf-trace-row-clickable"
                      onClick={() => toggleRow(i)}
                      data-testid="processing-trace-row"
                    >
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
                      <td className="bf-mono-cell" data-testid="processing-trace-row-total">
                        {formatMs(row.duration_ms)}
                      </td>
                      <td className="bf-mono-cell" data-testid="processing-trace-row-split">
                        {formatBlindfoldVsUpstream(row.duration_ms, row.upstream_duration_ms)}
                      </td>
                      <td className="bf-mono-cell">{row.detected}</td>
                      <td className="bf-mono-cell" data-testid="processing-trace-row-l3">
                        {row.l3_provider
                          ? `${row.l3_provider} (${formatMs(row.l3_duration_ms ?? 0)})`
                          : "—"}
                      </td>
                      <td>
                        <span
                          className="bf-trace-hops-toggle"
                          data-testid="processing-trace-row-hops-toggle"
                          data-expanded={expanded}
                        >
                          <span className="bf-mono-cell">{hopCount}</span>
                          <ChevronDown
                            size={14}
                            className={`bf-trace-hops-chevron${
                              expanded ? " bf-trace-hops-chevron--expanded" : ""
                            }`}
                          />
                        </span>
                      </td>
                    </tr>
                    {expanded && (
                      <tr className="bf-trace-expansion-row">
                        <td colSpan={7}>
                          <div
                            className="bf-trace-hop-cards"
                            data-testid="processing-trace-hop-cards"
                          >
                            {row.hops.map((hop) => (
                              <HopCard
                                key={hop.hop_index}
                                hop={hop}
                                workspace={workspace!}
                                canReveal={canReveal}
                              />
                            ))}
                            {hopCount === 0 && (
                              <p className="bf-empty">No hop detail for this exchange.</p>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={7} className="bf-empty">
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
