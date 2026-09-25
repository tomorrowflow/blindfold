// Payload inspection (issue #432, ADR-0059 amendment #431 §7): its own
// primary-nav destination -- a retained-exchange list on the left, the
// selected exchange's elided diff/replacements table/Reveal switch on the
// right. Previously the Processing trace's own fourth grain level (issue
// #400/#401/#403); this route replaces that embedding, not the underlying
// GET /v1/management/payload-inspection/leaves data it renders.
//
// The list's own row metadata (time, outcome, replacement count, excerpt)
// comes entirely from the retained exchange itself, per ADR-0059's own
// amendment: "the list does not depend on the Processing trace's ~200-record
// ring still holding that exchange." The detail pane's Reveal switch still
// needs each surrogate's reveal *lifecycle* (confirmed/pending/rejected),
// which is only ever classified on a Processing trace hop record -- so the
// detail pane (not the list) looks that up by exchange id, exactly as the
// former embedding did, and degrades to "nothing confirmed" if that record
// has since aged out of the trace's own ring (matching the pre-existing
// fallback everywhere else a token isn't found there).

import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Lock } from "../components/icons";
import { RetainedExchangeDetail, type ExchangeVerdict } from "../components/RetainedExchangeDetail";
import { useWorkspace } from "../components/WorkspaceContext";
import { fetchRewrittenLeaves, type RetainedExchange } from "../lib/rewrittenLeavesApi";
import { fetchProcessingTrace, type ProcessingTraceSurrogateLifecycle } from "../lib/processingTraceApi";
import { fetchUnprotectedModeActive } from "../lib/unprotectedModeApi";
import {
  DEFAULT_TIME_FILTER,
  TIME_PRESETS,
  filterExchanges,
  histogramBuckets,
  isBucketDimmed,
  presetCounts,
  replacementCountOf,
  type OutcomeFilter,
  type TimeFilter,
  type TimePreset,
} from "../lib/payloadInspectionFilters";

const POLL_INTERVAL_MS = 2000;

function formatTime(ts: string): string {
  const date = new Date(ts);
  return Number.isNaN(date.getTime()) ? ts : date.toLocaleTimeString();
}

function rowKey(exchange: RetainedExchange): string {
  return exchange.exchange_id ?? exchange.ts;
}

function excerptOf(exchange: RetainedExchange): string {
  const text = exchange.leaves[0]?.text ?? "";
  return text.length > 140 ? `${text.slice(0, 140)}…` : text;
}

const OUTCOME_FILTERS: { value: OutcomeFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "sent", label: "Sent" },
  { value: "never_sent", label: "Never sent" },
];

function timeFilterFromParams(params: URLSearchParams): TimeFilter {
  const hour = params.get("hour");
  if (hour) return { kind: "hour", hourKey: hour };
  const preset = params.get("time") as TimePreset | null;
  if (preset && TIME_PRESETS.some((p) => p.value === preset)) return { kind: "preset", preset };
  return DEFAULT_TIME_FILTER;
}

export function PayloadInspection() {
  const { activeWorkspace } = useWorkspace();
  const workspace = activeWorkspace?.slug ?? null;
  const canReveal = activeWorkspace?.roles.includes("re-identifier") ?? false;

  const [exchanges, setExchanges] = useState<RetainedExchange[]>([]);
  const [armed, setArmed] = useState(false);
  const [unprotectedActive, setUnprotectedActive] = useState(false);
  const [locked, setLocked] = useState(false);
  const [loading, setLoading] = useState(true);
  const [lifecycleByExchangeId, setLifecycleByExchangeId] = useState<
    Map<string, Map<string, ProcessingTraceSurrogateLifecycle>>
  >(new Map());
  const [verdictByExchangeId, setVerdictByExchangeId] = useState<Map<string, ExchangeVerdict>>(
    new Map()
  );
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const [searchParams, setSearchParams] = useSearchParams();
  const deepLinkExchangeId = searchParams.get("exchange");
  const appliedDeepLinkRef = useRef(false);

  // Filter state lives entirely in the URL (issue #434, ADR-0059 amendment
  // #431 §8) -- read fresh from `searchParams` every render rather than
  // mirrored into its own useState, so there is exactly one source of truth
  // and a filtered view survives reload/back-forward for free.
  const timeFilter = timeFilterFromParams(searchParams);
  const outcomeFilterParam = searchParams.get("outcome") as OutcomeFilter | null;
  const outcome: OutcomeFilter = OUTCOME_FILTERS.some((o) => o.value === outcomeFilterParam)
    ? (outcomeFilterParam as OutcomeFilter)
    : "all";
  const query = searchParams.get("q") ?? "";

  function updateParams(mutate: (next: URLSearchParams) => void) {
    const next = new URLSearchParams(searchParams);
    mutate(next);
    setSearchParams(next, { replace: true });
  }

  function setPreset(preset: TimePreset) {
    updateParams((next) => {
      next.delete("hour");
      if (preset === "all") next.delete("time");
      else next.set("time", preset);
    });
  }

  function setHour(hourKey: string) {
    updateParams((next) => {
      next.delete("time");
      next.set("hour", hourKey);
    });
  }

  function setOutcome(next: OutcomeFilter) {
    updateParams((params) => {
      if (next === "all") params.delete("outcome");
      else params.set("outcome", next);
    });
  }

  function setQuery(next: string) {
    updateParams((params) => {
      if (next.trim() === "") params.delete("q");
      else params.set("q", next);
    });
  }

  useEffect(() => {
    if (!workspace) return;
    let cancelled = false;
    function poll() {
      Promise.all([
        fetchRewrittenLeaves(workspace!),
        fetchProcessingTrace(workspace!),
        fetchUnprotectedModeActive(),
      ])
        .then(([leavesResult, traceResult, active]) => {
          if (cancelled) return;
          if (leavesResult.locked) {
            setLocked(true);
            setExchanges([]);
          } else {
            setLocked(false);
            setArmed(leavesResult.armed);
            // Newest first (ADR-0059 §7's own list ordering) -- the store
            // returns oldest-appended-first, same reversal ProcessingTrace.tsx
            // already applies to its own rows.
            setExchanges([...leavesResult.exchanges].reverse());
          }
          if (!traceResult.locked) {
            const lifecycles = new Map<string, Map<string, ProcessingTraceSurrogateLifecycle>>();
            const verdicts = new Map<string, ExchangeVerdict>();
            for (const record of traceResult.records) {
              if (!record.exchange_id) continue;
              const perToken = new Map<string, ProcessingTraceSurrogateLifecycle>();
              for (const hop of record.hops) {
                for (const surrogate of hop.surrogates) {
                  perToken.set(surrogate.token, surrogate.lifecycle);
                }
              }
              lifecycles.set(record.exchange_id, perToken);
              verdicts.set(record.exchange_id, {
                outcome: record.outcome === "blocked" ? "blocked" : "passed",
                reason: record.reason,
              });
            }
            setLifecycleByExchangeId(lifecycles);
            setVerdictByExchangeId(verdicts);
          }
          setUnprotectedActive(active);
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
  }, [workspace]);

  // Small, count-bounded dataset (<=200 exchanges, ADR-0059 amendment #431
  // §4) -- recomputed plainly on every render rather than memoized, so
  // `timeFilter`'s own shape never needs a bespoke dependency-array key.
  const now = Date.now();
  const filteredExchanges = filterExchanges(exchanges, { timeFilter, outcome, query }, now);
  const presetHitCounts = presetCounts(exchanges, outcome, query, now);
  const buckets = histogramBuckets(exchanges, outcome, query);
  const maxBucketCount = Math.max(0, ...buckets.map((b) => b.count));

  // Selection: a deep link from the Processing trace's own retained-payload
  // link is honored once (issue #432's own "the trace links to it instead of
  // embedding it"); afterwards the operator's own click wins, and reselecting
  // the newest VISIBLE row is a fallback for "nothing selected yet", "the
  // selected exchange is gone" (e.g. evicted by the retention ring), or --
  // issue #434 -- "a filter change just excluded the selected exchange,"
  // which must not strand the detail pane on a stale "no match" read.
  const timePresetOrHourKey = timeFilter.kind === "hour" ? timeFilter.hourKey : timeFilter.preset;
  useEffect(() => {
    setSelectedKey((prev) => {
      if (!appliedDeepLinkRef.current && deepLinkExchangeId) {
        const match = exchanges.find((e) => e.exchange_id === deepLinkExchangeId);
        if (match) {
          appliedDeepLinkRef.current = true;
          return rowKey(match);
        }
      }
      if (prev && filteredExchanges.some((e) => rowKey(e) === prev)) return prev;
      return filteredExchanges[0] ? rowKey(filteredExchanges[0]) : null;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [exchanges, deepLinkExchangeId, timeFilter.kind, timePresetOrHourKey, outcome, query]);

  if (!workspace) {
    return (
      <div className="bf-status-view">
        <h1>Payload inspection</h1>
        <p className="bf-empty">No workspace selected.</p>
      </div>
    );
  }

  const selected = filteredExchanges.find((e) => rowKey(e) === selectedKey) ?? null;
  const filteredReplacements = filteredExchanges.reduce((sum, e) => sum + replacementCountOf(e), 0);

  return (
    <div className="bf-status-view bf-payload-inspection" data-testid="payload-inspection-page">
      <div className="bf-status-header">
        <div>
          <h1>Payload inspection</h1>
          <p className="bf-card-subtitle">
            What Blindfold actually rewrote for a retained exchange — a
            transformation view, not a leak check.
          </p>
        </div>
      </div>

      {loading && <p className="bf-empty">Loading…</p>}

      {!loading && locked && (
        <div className="bf-audit-log-locked" data-testid="payload-inspection-locked">
          <Lock size={20} />
          <span>You need the viewer role to see Payload inspection for this workspace.</span>
        </div>
      )}

      {!loading && !locked && (
        <div className="bf-payload-inspection-layout">
          <aside className="bf-payload-inspection-sidebar">
            {exchanges.length > 0 && (
              <div className="bf-payload-inspection-filters" data-testid="payload-inspection-filters">
                <div
                  className="bf-payload-inspection-time-presets"
                  data-testid="payload-inspection-time-presets"
                >
                  {TIME_PRESETS.map(({ value, label }) => {
                    const count = presetHitCounts[value];
                    const isActive = timeFilter.kind === "preset" && timeFilter.preset === value;
                    return (
                      <button
                        key={value}
                        type="button"
                        className={`bf-payload-inspection-preset-chip${
                          isActive ? " bf-payload-inspection-preset-chip--active" : ""
                        }${count === 0 ? " bf-payload-inspection-preset-chip--dimmed" : ""}`}
                        aria-pressed={isActive}
                        data-testid={`payload-inspection-time-preset-${value}`}
                        onClick={() => setPreset(value)}
                      >
                        {label}{" "}
                        <span
                          className="bf-payload-inspection-preset-chip-count"
                          data-testid={`payload-inspection-time-preset-${value}-count`}
                        >
                          {count}
                        </span>
                      </button>
                    );
                  })}
                </div>

                {buckets.length > 0 && (
                  <div
                    className="bf-payload-inspection-histogram"
                    data-testid="payload-inspection-histogram"
                  >
                    {buckets.map((bucket) => {
                      const isSelected = timeFilter.kind === "hour" && timeFilter.hourKey === bucket.key;
                      const dimmed = isBucketDimmed(bucket, timeFilter, now);
                      return (
                        <button
                          key={bucket.key}
                          type="button"
                          className={`bf-payload-inspection-histogram-hour${
                            isSelected ? " bf-payload-inspection-histogram-hour--selected" : ""
                          }${dimmed ? " bf-payload-inspection-histogram-hour--dimmed" : ""}`}
                          aria-pressed={isSelected}
                          data-testid="payload-inspection-histogram-hour"
                          onClick={() => setHour(bucket.key)}
                        >
                          <span
                            className="bf-payload-inspection-histogram-hour-label"
                            data-testid="payload-inspection-histogram-hour-label"
                          >
                            {bucket.label}
                          </span>
                          <span
                            className="bf-payload-inspection-histogram-hour-bar"
                            data-testid="payload-inspection-histogram-hour-bar"
                            style={{ width: `${Math.round((bucket.count / maxBucketCount) * 100)}%` }}
                          />
                          <span
                            className="bf-payload-inspection-histogram-hour-count"
                            data-testid="payload-inspection-histogram-hour-count"
                          >
                            {bucket.count}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                )}

                <div
                  className="bf-payload-inspection-outcome-filter"
                  role="tablist"
                  aria-label="Outcome filter"
                  data-testid="payload-inspection-outcome-filter"
                >
                  {OUTCOME_FILTERS.map(({ value, label }) => (
                    <button
                      key={value}
                      type="button"
                      role="tab"
                      aria-selected={outcome === value}
                      className={`bf-search-mode-option${
                        outcome === value ? " bf-search-mode-option--active" : ""
                      }`}
                      data-testid={`payload-inspection-outcome-filter-${value}`}
                      onClick={() => setOutcome(value)}
                    >
                      {label}
                    </button>
                  ))}
                </div>

                <input
                  type="search"
                  className="bf-payload-inspection-search"
                  data-testid="payload-inspection-search"
                  placeholder="Search retained text…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </div>
            )}
            <div className="bf-payload-inspection-count" data-testid="payload-inspection-count">
              {filteredExchanges.length} of {exchanges.length} exchange
              {exchanges.length === 1 ? "" : "s"} · {filteredReplacements} replacement
              {filteredReplacements === 1 ? "" : "s"}
            </div>
            <ul className="bf-payload-inspection-list" data-testid="payload-inspection-list">
              {filteredExchanges.map((exchange) => {
                const key = rowKey(exchange);
                const isSelected = key === selectedKey;
                return (
                  <li key={key}>
                    <button
                      type="button"
                      className={`bf-payload-inspection-row${
                        isSelected ? " bf-payload-inspection-row--selected" : ""
                      }`}
                      data-testid="payload-inspection-row"
                      aria-current={isSelected || undefined}
                      onClick={() => setSelectedKey(key)}
                    >
                      <span
                        className={`bf-payload-inspection-row-dot${
                          exchange.blocked
                            ? " bf-payload-inspection-row-dot--blocked"
                            : " bf-payload-inspection-row-dot--sent"
                        }`}
                        aria-hidden="true"
                      />
                      <span className="bf-payload-inspection-row-time">
                        {formatTime(exchange.ts)}
                      </span>
                      <span
                        className="bf-payload-inspection-row-outcome"
                        data-testid="payload-inspection-row-outcome"
                      >
                        {exchange.blocked ? "Never sent" : "Sent"}
                      </span>
                      <span
                        className="bf-payload-inspection-row-count"
                        data-testid="payload-inspection-row-count"
                      >
                        {replacementCountOf(exchange)}
                      </span>
                      <span
                        className="bf-payload-inspection-row-excerpt"
                        data-testid="payload-inspection-row-excerpt"
                      >
                        {excerptOf(exchange)}
                      </span>
                    </button>
                  </li>
                );
              })}
              {exchanges.length === 0 && (
                <li className="bf-empty" data-testid="payload-inspection-list-empty">
                  {!armed
                    ? "Payload inspection is disarmed."
                    : unprotectedActive
                      ? "Unprotected mode is active."
                      : "Nothing retained yet."}
                </li>
              )}
              {exchanges.length > 0 && filteredExchanges.length === 0 && (
                <li className="bf-empty" data-testid="payload-inspection-list-filtered-empty">
                  No retained exchanges match these filters.
                </li>
              )}
            </ul>
          </aside>
          <div className="bf-payload-inspection-detail">
            {selected ? (
              <RetainedExchangeDetail
                key={rowKey(selected)}
                exchange={selected}
                workspace={workspace}
                canReveal={canReveal}
                lifecycleByToken={
                  (selected.exchange_id && lifecycleByExchangeId.get(selected.exchange_id)) ||
                  new Map()
                }
                verdict={
                  (selected.exchange_id && verdictByExchangeId.get(selected.exchange_id)) || {
                    outcome: selected.blocked ? "blocked" : "passed",
                    reason: null,
                  }
                }
              />
            ) : !armed ? (
              <p className="bf-empty" data-testid="payload-inspection-disarmed">
                Payload inspection is disarmed, so nothing is retained for any exchange.{" "}
                <Link to="/settings" data-testid="payload-inspection-arm-link">
                  Arm it in Settings →
                </Link>
              </p>
            ) : unprotectedActive ? (
              <p className="bf-empty" data-testid="payload-inspection-unprotected-active">
                Unprotected mode is active — the blindfolding pipeline is skipped
                entirely while it's on, so there is nothing entity-free to retain.
              </p>
            ) : exchanges.length > 0 ? (
              <p className="bf-empty" data-testid="payload-inspection-filtered-empty">
                No retained exchange matches the current filters.
              </p>
            ) : (
              <p className="bf-empty" data-testid="payload-inspection-no-retained">
                Payload inspection is armed, but nothing has been retained yet.
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
